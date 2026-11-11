"""The journal's background writer.

Changes, refusals and errors are written to the journal at once, by the request
that caused them. Plain views are most of the traffic, so they wait here and go
to the database in batches about once a second: a page that loads does not pay
for a write of its own.

The writer also keeps the device registry (who came from which device and
address), folds the same view repeated over and over into one row with a
counter, and removes views older than the retention period.

State lives in this process. With several API processes each keeps its own and
writes it within a second; reading the journal first writes what this process
still holds.
"""

import asyncio
import json
import logging
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache

from app.config import get_settings
from app.database import pool
from app.utils.audit import INSERT_SQL, RequestFacts, audit_row, request_facts
from app.utils.client_info import DeviceTraits, is_identified, parse_user_agent

logger = logging.getLogger(__name__)

FLUSH_SECONDS = 1.0
#: More views than this waiting for the database means it is not keeping up:
#: new ones are dropped (and counted) so the API does not run out of memory.
MAX_PENDING_ROWS = 20_000
MAX_REPEAT_ENTRIES = 20_000
CLEANUP_EVERY_SECONDS = 6 * 3600
CLEANUP_BATCH = 5_000
CLEANUP_MAX_BATCHES = 40
#: So that several API processes do not clean the journal at the same moment.
CLEANUP_LOCK_KEY = 7_211_402

REPEAT_SQL = """
    UPDATE audit_logs
    SET details = COALESCE(details, '{}'::jsonb) || $2::jsonb
    WHERE request_id = $1
"""

DEVICE_KNOWN_SQL = """
    SELECT device_key, user_agent, client
    FROM client_devices
    WHERE device_key = ANY($1::text[])
"""

# A device seen for the first time, or one whose browser changed (an update):
# what its User-Agent says is written anew.
DEVICE_UPSERT_SQL = """
    INSERT INTO client_devices (
        device_key, user_agent, os_name, os_version, browser_name, browser_version,
        device_type, device_model, is_bot,
        first_seen_at, last_seen_at, first_ip, last_ip, last_country, last_user_id,
        requests_count
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $10, $11, $11, $12, $13, $14)
    ON CONFLICT (device_key) DO UPDATE SET
        user_agent = COALESCE(EXCLUDED.user_agent, client_devices.user_agent),
        os_name = EXCLUDED.os_name,
        os_version = EXCLUDED.os_version,
        browser_name = EXCLUDED.browser_name,
        browser_version = EXCLUDED.browser_version,
        device_type = EXCLUDED.device_type,
        device_model = COALESCE(EXCLUDED.device_model, client_devices.device_model),
        is_bot = EXCLUDED.is_bot,
        last_seen_at = GREATEST(client_devices.last_seen_at, EXCLUDED.last_seen_at),
        last_ip = COALESCE(EXCLUDED.last_ip, client_devices.last_ip),
        last_country = COALESCE(EXCLUDED.last_country, client_devices.last_country),
        last_user_id = COALESCE(EXCLUDED.last_user_id, client_devices.last_user_id),
        requests_count = client_devices.requests_count + EXCLUDED.requests_count
"""

DEVICE_TOUCH_SQL = """
    UPDATE client_devices
    SET last_seen_at = GREATEST(last_seen_at, $2),
        last_ip = COALESCE($3, last_ip),
        last_country = COALESCE($4, last_country),
        last_user_id = COALESCE($5, last_user_id),
        requests_count = requests_count + $6
    WHERE device_key = $1
"""

DEVICE_USER_SQL = """
    INSERT INTO client_device_users (
        device_key, user_id, first_seen_at, last_seen_at, last_ip, requests_count
    )
    VALUES ($1, $2, $3, $3, $4, $5)
    ON CONFLICT (device_key, user_id) DO UPDATE SET
        last_seen_at = GREATEST(client_device_users.last_seen_at, EXCLUDED.last_seen_at),
        last_ip = COALESCE(EXCLUDED.last_ip, client_device_users.last_ip),
        requests_count = client_device_users.requests_count + EXCLUDED.requests_count
"""

DEVICE_IP_SQL = """
    INSERT INTO client_device_ips (
        device_key, ip_address, country, first_seen_at, last_seen_at, requests_count
    )
    VALUES ($1, $2, $3, $4, $4, $5)
    ON CONFLICT (device_key, ip_address) DO UPDATE SET
        country = COALESCE(EXCLUDED.country, client_device_ips.country),
        last_seen_at = GREATEST(client_device_ips.last_seen_at, EXCLUDED.last_seen_at),
        requests_count = client_device_ips.requests_count + EXCLUDED.requests_count
"""

# Plain views and opened pages older than the retention period. Everything that
# changed something, was refused or failed stays.
CLEANUP_SQL = """
    DELETE FROM audit_logs
    WHERE id IN (
        SELECT id FROM audit_logs
        WHERE created_at < NOW() - make_interval(days => $1)
          AND (action IN ('http.get', 'http.head') OR action LIKE 'page.%')
          AND COALESCE(status_code, 200) < 400
        ORDER BY created_at
        LIMIT $2
    )
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


@lru_cache(maxsize=2048)
def _traits(user_agent: str | None, platform, platform_version, model, mobile) -> DeviceTraits:
    return parse_user_agent(
        user_agent,
        {"platform": platform, "platform_version": platform_version, "model": model, "mobile": mobile},
    )


def traits_for(user_agent: str | None, hints: dict | None, report: dict | None = None) -> DeviceTraits:
    """What a device is, from its User-Agent, its headers and its own report."""
    hints = hints or {}
    report = report or {}
    mobile = hints.get("mobile")
    if mobile is None and isinstance(report.get("mobile"), bool):
        mobile = "?1" if report["mobile"] else "?0"
    return _traits(
        user_agent,
        hints.get("platform") or report.get("platform"),
        hints.get("platform_version") or report.get("platform_version"),
        hints.get("model") or report.get("model"),
        mobile,
    )


@dataclass
class _Touch:
    """What one device did since the last write."""

    user_agent: str | None
    hints: dict
    last_at: datetime
    last_ip: str | None = None
    last_country: str | None = None
    last_user_id: int | None = None
    count: int = 0
    users: dict[int, int] = field(default_factory=dict)
    ips: dict[str, tuple[int, str | None]] = field(default_factory=dict)


@dataclass
class _Repeat:
    """A view that has been written, and how many times it came again."""

    request_id: uuid.UUID
    started: float
    count: int = 0
    last_at: datetime | None = None


class Journal:
    def __init__(self) -> None:
        self._rows: list[tuple] = []
        self._touches: dict[str, _Touch] = {}
        self._repeats: OrderedDict[tuple, _Repeat] = OrderedDict()
        self._updates: list[tuple[uuid.UUID, str]] = []
        self._task: asyncio.Task | None = None
        self._next_cleanup = 0.0
        self.dropped = 0

    # --- what requests tell the writer -------------------------------------------------

    def touch(self, facts: RequestFacts) -> None:
        """Note that a device was here. Called for every request, also unwritten ones."""
        if not facts.device_key:
            return
        seen = self._touches.get(facts.device_key)
        if seen is None:
            seen = self._touches[facts.device_key] = _Touch(
                user_agent=facts.user_agent, hints=dict(facts.hints), last_at=_now()
            )
        seen.count += 1
        seen.last_at = _now()
        seen.user_agent = facts.user_agent or seen.user_agent
        if facts.ip and facts.ip != "-":
            seen.last_ip = facts.ip
            count, _country = seen.ips.get(facts.ip, (0, None))
            seen.ips[facts.ip] = (count + 1, facts.country)
        seen.last_country = facts.country or seen.last_country
        if facts.user_id is not None:
            seen.last_user_id = facts.user_id
            seen.users[facts.user_id] = seen.users.get(facts.user_id, 0) + 1

    def link(self, facts: RequestFacts, user_id: int) -> None:
        """A sign-in: from now on the device is this person's as well.

        The request that signs in carries no access token yet, so `touch` alone
        would not know whose device it is.
        """
        if not facts.device_key:
            return
        seen = self._touches.get(facts.device_key)
        if seen is None:
            seen = self._touches[facts.device_key] = _Touch(
                user_agent=facts.user_agent, hints=dict(facts.hints), last_at=_now()
            )
            if facts.ip and facts.ip != "-":
                seen.last_ip = facts.ip
        seen.last_user_id = user_id
        seen.users[user_id] = seen.users.get(user_id, 0) + 1

    def pending_link(self, device_key: str, user_id: int) -> bool:
        seen = self._touches.get(device_key)
        return bool(seen and user_id in seen.users)

    def view(
        self,
        facts: RequestFacts,
        *,
        method: str,
        path: str,
        query: dict | None,
        status_code: int,
        duration_ms: int,
        actor_id: int | None,
        company_id: int | None,
        resource_type: str,
        resource_id: str,
        repeat_key: tuple | None = None,
    ) -> None:
        """Queue a view; the same view repeated within the window is only counted."""
        window = get_settings().journal_repeat_window_seconds
        if repeat_key is not None:
            # Refusals of the limiter come by the thousand: they are always folded.
            window = max(window, 60)
        key = repeat_key or (
            facts.device_key,
            facts.user_id,
            method,
            path,
            json.dumps(query, sort_keys=True, default=str) if query else "",
        )
        now = time.monotonic()
        if window > 0:
            again = self._repeats.get(key)
            if again is not None and now - again.started < window:
                again.count += 1
                again.last_at = _now()
                return
            if again is not None:
                self._close(key, again)
        if len(self._rows) >= MAX_PENDING_ROWS:
            self.dropped += 1
            return
        if window > 0:
            self._repeats[key] = _Repeat(request_id=facts.request_id, started=now)
            while len(self._repeats) > MAX_REPEAT_ENTRIES:
                oldest_key, oldest = next(iter(self._repeats.items()))
                self._close(oldest_key, oldest)
        self._rows.append(
            audit_row(
                user_id=facts.user_id,
                actor_id=actor_id,
                company_id=company_id,
                action=f"http.{method.lower()}",
                resource_type=resource_type,
                resource_id=resource_id,
                details={"query": query} if query else None,
                ip_address=facts.ip,
                user_agent=facts.user_agent,
                created_at=_now(),
                method=method,
                path=path,
                status_code=status_code,
                duration_ms=duration_ms,
                request_id=facts.request_id,
                device_key=facts.device_key,
                session_key=facts.session_key,
                country=facts.country,
            )
        )

    def _close(self, key: tuple, entry: _Repeat) -> None:
        self._repeats.pop(key, None)
        if entry.count:
            note = {
                "repeats": entry.count,
                "repeated_until": (entry.last_at or _now()).isoformat(),
            }
            self._updates.append((entry.request_id, json.dumps(note)))

    def _expire(self, everything: bool = False) -> None:
        window = max(get_settings().journal_repeat_window_seconds, 60)
        now = time.monotonic()
        for key, entry in list(self._repeats.items()):
            if not everything and now - entry.started < window:
                break  # the rest started later
            self._close(key, entry)

    # --- writing -----------------------------------------------------------------------

    async def flush(self, everything: bool = False) -> None:
        """Write what is waiting. ``everything`` also closes repeat counters still open."""
        self._expire(everything)
        if not (self._rows or self._touches or self._updates) or not pool.ready:
            return
        # Taken in one step, before any await: a request that arrives while the
        # batch is being written starts the next one.
        rows, self._rows = self._rows, []
        touches, self._touches = self._touches, {}
        updates, self._updates = self._updates, []
        if self.dropped:
            logger.warning("Journal dropped %s views: the database is not keeping up", self.dropped)
            self.dropped = 0
        try:
            async with pool.acquire() as conn:
                if rows:
                    await conn.executemany(INSERT_SQL, rows)
                if updates:
                    await conn.executemany(REPEAT_SQL, updates)
                if touches:
                    await self._write_devices(conn, touches)
        except Exception:
            logger.exception("Journal write failed (%s rows lost)", len(rows))

    async def _write_devices(self, conn, touches: dict[str, _Touch]) -> None:
        known = {
            row["device_key"]: row
            for row in await conn.fetch(DEVICE_KNOWN_SQL, list(touches))
        }
        upserts, plain, users, ips = [], [], [], []
        for key, seen in touches.items():
            before = known.get(key)
            if before is None or (seen.user_agent and before["user_agent"] != seen.user_agent):
                report = before["client"] if before else None
                if isinstance(report, str):
                    report = json.loads(report)
                traits = traits_for(seen.user_agent, seen.hints, report)
                upserts.append(
                    (
                        key,
                        (seen.user_agent or "")[:500] or None,
                        traits.os_name,
                        traits.os_version,
                        traits.browser_name,
                        traits.browser_version,
                        traits.device_type,
                        (traits.device_model or "")[:80] or None,
                        traits.is_bot,
                        seen.last_at,
                        seen.last_ip,
                        seen.last_country,
                        seen.last_user_id,
                        seen.count,
                    )
                )
            else:
                plain.append(
                    (key, seen.last_at, seen.last_ip, seen.last_country, seen.last_user_id, seen.count)
                )
            users.extend(
                (key, user_id, seen.last_at, seen.last_ip, count) for user_id, count in seen.users.items()
            )
            ips.extend(
                (key, ip[:50], country, seen.last_at, count) for ip, (count, country) in seen.ips.items()
            )
        if upserts:
            await conn.executemany(DEVICE_UPSERT_SQL, upserts)
        if plain:
            await conn.executemany(DEVICE_TOUCH_SQL, plain)
        if users:
            await conn.executemany(DEVICE_USER_SQL, users)
        if ips:
            await conn.executemany(DEVICE_IP_SQL, ips)

    async def cleanup(self) -> int:
        """Remove plain views older than the retention period; returns how many."""
        days = get_settings().journal_view_retention_days
        if days <= 0 or not pool.ready:
            return 0
        removed = 0
        async with pool.acquire() as conn:
            if not await conn.fetchval("SELECT pg_try_advisory_lock($1)", CLEANUP_LOCK_KEY):
                return 0
            try:
                for _ in range(CLEANUP_MAX_BATCHES):
                    status = await conn.execute(CLEANUP_SQL, days, CLEANUP_BATCH)
                    batch = int(status.rsplit(" ", 1)[-1])
                    removed += batch
                    if batch < CLEANUP_BATCH:
                        break
            finally:
                await conn.execute("SELECT pg_advisory_unlock($1)", CLEANUP_LOCK_KEY)
        if removed:
            logger.info("Journal: removed %s views older than %s days", removed, days)
        return removed

    async def _run(self) -> None:
        # The first cleanup waits a little, so a restart is not slowed by it.
        self._next_cleanup = time.monotonic() + 120
        while True:
            await asyncio.sleep(FLUSH_SECONDS)
            await self.flush()
            if time.monotonic() >= self._next_cleanup:
                self._next_cleanup = time.monotonic() + CLEANUP_EVERY_SECONDS
                try:
                    await self.cleanup()
                except Exception:
                    logger.exception("Journal cleanup failed")

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="journal-writer")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        await self.flush(everything=True)


journal = Journal()


async def _is_new_device(conn, user_id: int, device_key: str) -> bool:
    row = await conn.fetchrow(
        """
        SELECT
            EXISTS (
                SELECT 1 FROM client_device_users
                WHERE user_id = $1 AND device_key = $2
            ) AS known,
            EXISTS (
                SELECT 1 FROM client_device_users
                WHERE user_id = $1 AND device_key <> $2 AND device_key NOT LIKE 'h-%'
            ) AS has_other
        """,
        user_id,
        device_key,
    )
    return bool(row and row["has_other"] and not row["known"])


#: Notices being sent; kept so that they are not dropped half-way.
_background: set[asyncio.Task] = set()
#: Where "change the password" lives in each cabinet.
SECURITY_PAGES = {
    "buyer": "/customer/profile?tab=security",
    "supplier": "/supplier/profile?tab=security",
}


def _warn_new_device(user_id: int, facts: RequestFacts, side: str | None) -> None:
    """Tell the person that their account was entered from a device they never used.

    Sent after the sign-in has answered (and repeated to their Telegram, if they
    linked it): somebody else knowing the password is found out by its owner.
    """

    async def send() -> None:
        try:
            from app.modules.notifications.services import NotificationService

            traits = traits_for(facts.user_agent, facts.hints)
            names = [
                " ".join(part for part in pair if part)
                for pair in (
                    (traits.os_name, traits.os_version),
                    (traits.browser_name, traits.browser_version),
                )
            ]
            device = " · ".join(name for name in names if name) or "неизвестное устройство"
            address = f", IP {facts.ip}" if facts.ip and facts.ip != "-" else ""
            await NotificationService().notify_user(
                user_id,
                "system",
                "Вход с нового устройства",
                f"В ваш аккаунт вошли с устройства, с которого вы раньше не заходили: {device}{address}. "
                "Если это были не вы, смените пароль.",
                href=SECURITY_PAGES.get(side or ""),
            )
        except Exception:
            logger.exception("New-device notice failed")

    task = asyncio.get_running_loop().create_task(send(), name="new-device-notice")
    _background.add(task)
    task.add_done_callback(_background.discard)


async def sign_in_details(
    conn, user_id: int, details: dict | None = None, side: str | None = None
) -> dict | None:
    """Details of a sign-in row, marked when the person has never used this device.

    The very first device of an account is not "new": there is nothing to compare
    it with. Also ties the device to the person, and tells them about a new one.
    Never breaks the sign-in itself.
    """
    facts = request_facts.get()
    if not facts or not is_identified(facts.device_key):
        return details
    fresh = False
    try:
        # A pair this process has seen but not written yet is known as well. It is
        # looked up in memory: writing it out here would take a second connection
        # while the sign-in holds one.
        if not journal.pending_link(facts.device_key, user_id):
            if conn.is_in_transaction():
                async with conn.transaction():
                    fresh = await _is_new_device(conn, user_id, facts.device_key)
            else:
                fresh = await _is_new_device(conn, user_id, facts.device_key)
    except Exception:
        logger.exception("New-device check failed")
    journal.link(facts, user_id)
    if not fresh:
        return details
    _warn_new_device(user_id, facts, side)
    return {**(details or {}), "new_device": True}
