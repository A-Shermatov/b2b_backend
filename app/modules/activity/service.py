"""Pages people open on the site, and what their browsers say about themselves."""

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from app.core.journal import traits_for
from app.core.middleware._common import PAGE_SECRET_KEYS, is_sensitive_key, safe_path
from app.database import pool
from app.modules.activity.schemas import MAX_QUERY_KEYS, ActivityBatch, DeviceReport, PageEvent
from app.utils.audit import INSERT_SQL, RequestFacts, audit_row
from app.utils.client_info import fingerprint, is_identified

logger = logging.getLogger(__name__)

PAGE_VIEW = "page.view"
_OBJECT_ID = re.compile(r"^(\d+|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F-]{4,})$")
QUERY_VALUE_LIMIT = 200

# The browser's report replaces what was read from the headers alone.
REPORT_SQL = """
    INSERT INTO client_devices (
        device_key, user_agent, os_name, os_version, browser_name, browser_version,
        device_type, device_model, is_bot, language, timezone, screen,
        client, reported_at, fingerprint,
        first_seen_at, last_seen_at, first_ip, last_ip, last_country, last_user_id
    )
    VALUES (
        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
        $13::jsonb, NOW(), $14,
        NOW(), NOW(), $15, $15, $16, $17
    )
    ON CONFLICT (device_key) DO UPDATE SET
        user_agent = COALESCE(EXCLUDED.user_agent, client_devices.user_agent),
        os_name = EXCLUDED.os_name,
        os_version = EXCLUDED.os_version,
        browser_name = EXCLUDED.browser_name,
        browser_version = EXCLUDED.browser_version,
        device_type = EXCLUDED.device_type,
        device_model = COALESCE(EXCLUDED.device_model, client_devices.device_model),
        is_bot = EXCLUDED.is_bot,
        language = EXCLUDED.language,
        timezone = EXCLUDED.timezone,
        screen = EXCLUDED.screen,
        client = EXCLUDED.client,
        reported_at = NOW(),
        fingerprint = EXCLUDED.fingerprint,
        last_seen_at = NOW(),
        last_ip = COALESCE(EXCLUDED.last_ip, client_devices.last_ip),
        last_country = COALESCE(EXCLUDED.last_country, client_devices.last_country),
        last_user_id = COALESCE(EXCLUDED.last_user_id, client_devices.last_user_id)
"""

# A view is ended only by the device that opened it, and only while it is fresh.
LEAVE_SQL = """
    UPDATE audit_logs
    SET duration_ms = $2,
        details = COALESCE(details, '{}'::jsonb) || $3::jsonb
    WHERE request_id = $1
      AND action = 'page.view'
      AND device_key = $4
      AND created_at > NOW() - INTERVAL '2 days'
"""


def _screen(report: DeviceReport) -> str | None:
    if not report.screen_width or not report.screen_height:
        return None
    text = f"{report.screen_width}×{report.screen_height}"
    if report.pixel_ratio and report.pixel_ratio != 1:
        text += f" @{report.pixel_ratio:g}x"
    return text


def page_path(raw: str | None) -> str | None:
    """A page of the site as it may be kept, or None for anything else."""
    text = (raw or "").split("?", 1)[0].split("#", 1)[0]
    if not text.startswith("/") or text.startswith("//"):
        return None
    return safe_path(text)


def page_query(query: dict | None) -> dict | None:
    """Search parameters of a page, with one-time tokens hidden."""
    if not query:
        return None
    kept = {}
    for key, value in list(query.items())[:MAX_QUERY_KEYS]:
        name = str(key)[:60]
        secret = is_sensitive_key(name) or name.lower() in PAGE_SECRET_KEYS
        kept[name] = "***" if secret else str(value)[:QUERY_VALUE_LIMIT]
    return kept or None


def _object_id(path: str) -> str:
    for part in reversed(path.rstrip("/").split("/")):
        if _OBJECT_ID.match(part):
            return part[:100]
    return "-"


def _moment(event: PageEvent, now: datetime) -> datetime:
    return now - timedelta(milliseconds=event.ago_ms or 0)


async def save_report(conn, facts: RequestFacts, report: DeviceReport) -> None:
    data = report.model_dump(exclude_none=True)
    data = {key: value for key, value in data.items() if value not in ([], "")}
    traits = traits_for(facts.user_agent, facts.hints, data)
    await conn.execute(
        REPORT_SQL,
        facts.device_key,
        (facts.user_agent or "")[:500] or None,
        traits.os_name,
        traits.os_version,
        traits.browser_name,
        traits.browser_version,
        traits.device_type,
        (traits.device_model or "")[:80] or None,
        traits.is_bot,
        report.language or (report.languages[0] if report.languages else None),
        report.timezone,
        _screen(report),
        json.dumps(data, ensure_ascii=False),
        fingerprint(data),
        facts.ip if facts.ip and facts.ip != "-" else None,
        facts.country,
        facts.user_id,
    )


async def record(batch: ActivityBatch, facts: RequestFacts, actor_id: int | None, company_id: int | None) -> int:
    """Keep the pages a person opened and what their browser reported. Returns pages kept."""
    now = datetime.now(timezone.utc)
    views, leaves = [], []
    for event in batch.events:
        if event.type == "leave":
            note = {"seconds": event.seconds or 0}
            if event.active_seconds is not None:
                note["active_seconds"] = min(event.active_seconds, event.seconds or 0)
            leaves.append(
                (event.id, (event.seconds or 0) * 1000, json.dumps(note), facts.device_key)
            )
            continue
        path = page_path(event.path)
        if path is None:
            continue
        details = {
            "title": event.title,
            "query": page_query(event.query),
            "referrer": event.referrer,
        }
        details = {key: value for key, value in details.items() if value}
        views.append(
            audit_row(
                user_id=facts.user_id,
                actor_id=actor_id,
                company_id=company_id,
                action=PAGE_VIEW,
                resource_type="page",
                resource_id=_object_id(path),
                details=details or None,
                ip_address=facts.ip,
                user_agent=facts.user_agent,
                created_at=_moment(event, now),
                path=path,
                request_id=event.id,
                device_key=facts.device_key,
                session_key=facts.session_key,
                country=facts.country,
            )
        )

    report = batch.device if is_identified(facts.device_key) else None
    if not (views or leaves or report) or not pool.ready:
        return 0
    async with pool.acquire() as conn:
        if report:
            await save_report(conn, facts, report)
        if views:
            await conn.executemany(INSERT_SQL, views)
        if leaves:
            await conn.executemany(LEAVE_SQL, leaves)
    return len(views)
