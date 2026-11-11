"""The registry of devices the platform has seen: for the administrator who looks
into an account ("where did they sign in from?") or into a device ("who else
uses it?")."""

from app.core.exceptions import NotFoundError
from app.core.journal import journal
from app.modules.admin.schemas.logs import AdminDeviceDetail, AdminDeviceItem
from app.modules.admin.services.common import normalize_query, pages_for, parse_json
from app.modules.admin.services.logs import PERIOD_INTERVALS, joined
from app.utils.client_info import DERIVED_PREFIX, is_identified

USERS_PREVIEW = 3
DETAIL_LIMIT = 50
SIMILAR_LIMIT = 10
# A key made from headers stands for "this address and this browser", which
# several people behind one router share: it never counts as a shared device.
DERIVED_LIKE = f"{DERIVED_PREFIX}%"

DEVICE_COLUMNS = """
    d.device_key, d.user_agent, d.os_name, d.os_version, d.browser_name, d.browser_version,
    d.device_type, d.device_model, d.is_bot, d.language, d.timezone, d.screen, d.client,
    d.reported_at, d.fingerprint, d.first_seen_at, d.last_seen_at, d.last_ip,
    d.last_country, d.requests_count,
    (SELECT COUNT(*) FROM client_device_users du WHERE du.device_key = d.device_key) AS users_count,
    (SELECT COUNT(*) FROM client_device_ips di WHERE di.device_key = d.device_key) AS ips_count
"""
USERS_OF_DEVICES_SQL = """
    SELECT du.device_key, du.first_seen_at, du.last_seen_at, du.last_ip, du.requests_count,
           u.id, u.email, u.first_name, u.last_name, u.role::text AS role
    FROM client_device_users du
    JOIN users u ON u.id = du.user_id
    WHERE du.device_key = ANY($1::text[])
    ORDER BY du.last_seen_at DESC
"""
FLAG_SQL = {
    "shared": f"""(d.device_key NOT LIKE '{DERIVED_LIKE}' AND (
        SELECT COUNT(*) FROM client_device_users du WHERE du.device_key = d.device_key
    ) > 1)""",
    "bot": "d.is_bot",
    "automated": "d.client->>'webdriver' = 'true'",
    "unidentified": f"d.device_key LIKE '{DERIVED_LIKE}'",
}


def _user(row) -> dict:
    name = f"{row['first_name'] or ''} {row['last_name'] or ''}".strip()
    return {"id": row["id"], "email": row["email"], "name": name or row["email"], "role": row["role"]}


def _item(row, users: list[dict]) -> dict:
    client = parse_json(row["client"])
    return {
        "key": row["device_key"],
        "identified": is_identified(row["device_key"]),
        "type": row["device_type"] or "other",
        "os": joined(row["os_name"], row["os_version"]),
        "browser": joined(row["browser_name"], row["browser_version"]),
        "model": row["device_model"],
        "is_bot": bool(row["is_bot"]),
        "automated": isinstance(client, dict) and client.get("webdriver") is True,
        "language": row["language"],
        "timezone": row["timezone"],
        "screen": row["screen"],
        "country": row["last_country"],
        "first_seen_at": row["first_seen_at"],
        "last_seen_at": row["last_seen_at"],
        "last_ip": row["last_ip"],
        "requests_count": int(row["requests_count"] or 0),
        "users_count": int(row["users_count"] or 0),
        "ips_count": int(row["ips_count"] or 0),
        "users": users,
    }


async def _users_by_device(conn, keys: list[str]) -> dict[str, list]:
    grouped: dict[str, list] = {key: [] for key in keys}
    if keys:
        for row in await conn.fetch(USERS_OF_DEVICES_SQL, keys):
            grouped[row["device_key"]].append(row)
    return grouped


class AdminDevicesMixin:
    async def list_devices(
        self,
        query: str | None = None,
        user_id: int | None = None,
        ip: str | None = None,
        flag: str | None = None,
        device_type: str | None = None,
        period: str = "all",
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        await journal.flush()
        params: list = []
        clauses = ["TRUE"]

        def param(value) -> str:
            params.append(value)
            return f"${len(params)}"

        if period in PERIOD_INTERVALS:
            clauses.append(f"d.last_seen_at >= NOW() - {param(PERIOD_INTERVALS[period])}::interval")
        if user_id is not None:
            clauses.append(
                f"""EXISTS (SELECT 1 FROM client_device_users du
                    WHERE du.device_key = d.device_key AND du.user_id = {param(user_id)})"""
            )
        if ip and ip.strip():
            clauses.append(
                f"""EXISTS (SELECT 1 FROM client_device_ips di
                    WHERE di.device_key = d.device_key AND di.ip_address = {param(ip.strip())})"""
            )
        if flag in FLAG_SQL:
            clauses.append(FLAG_SQL[flag])
        if device_type:
            clauses.append(f"d.device_type = {param(device_type)}")
        normalized = normalize_query(query)
        if normalized:
            like = param(f"%{normalized}%")
            clauses.append(
                f"""(
                    concat_ws(' ', d.os_name, d.os_version, d.browser_name, d.browser_version,
                              d.device_model) ILIKE {like}
                    OR d.user_agent ILIKE {like}
                    OR d.device_key ILIKE {like}
                    OR EXISTS (
                        SELECT 1 FROM client_device_ips di
                        WHERE di.device_key = d.device_key AND di.ip_address ILIKE {like}
                    )
                    OR EXISTS (
                        SELECT 1 FROM client_device_users du
                        JOIN users u ON u.id = du.user_id
                        WHERE du.device_key = d.device_key
                          AND (u.email ILIKE {like}
                               OR concat_ws(' ', u.first_name, u.last_name) ILIKE {like})
                    )
                )"""
            )
        where = " AND ".join(clauses)

        async with self.connection() as conn:
            total = int(
                await conn.fetchval(f"SELECT COUNT(*) FROM client_devices d WHERE {where}", *params)
                or 0
            )
            rows = await conn.fetch(
                f"""
                SELECT {DEVICE_COLUMNS}
                FROM client_devices d
                WHERE {where}
                ORDER BY d.last_seen_at DESC, d.device_key
                LIMIT {param(page_size)} OFFSET {param((page - 1) * page_size)}
                """,
                *params,
            )
            users = await _users_by_device(conn, [row["device_key"] for row in rows])

        return {
            "items": [
                AdminDeviceItem.model_validate(
                    _item(row, [_user(user) for user in users[row["device_key"]][:USERS_PREVIEW]])
                )
                for row in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages_for(total, page_size),
        }

    async def get_device(self, device_key: str) -> AdminDeviceDetail:
        await journal.flush()
        async with self.connection() as conn:
            row = await conn.fetchrow(
                f"SELECT {DEVICE_COLUMNS} FROM client_devices d WHERE d.device_key = $1",
                device_key,
            )
            if not row:
                raise NotFoundError("Device not found")
            users = (await _users_by_device(conn, [device_key]))[device_key][:DETAIL_LIMIT]
            ips = await conn.fetch(
                """
                SELECT ip_address, country, first_seen_at, last_seen_at, requests_count
                FROM client_device_ips
                WHERE device_key = $1
                ORDER BY last_seen_at DESC
                LIMIT $2
                """,
                device_key,
                DETAIL_LIMIT,
            )
            similar_rows = []
            if row["fingerprint"]:
                similar_rows = await conn.fetch(
                    f"""
                    SELECT {DEVICE_COLUMNS}
                    FROM client_devices d
                    WHERE d.fingerprint = $1 AND d.device_key <> $2
                    ORDER BY d.last_seen_at DESC
                    LIMIT $3
                    """,
                    row["fingerprint"],
                    device_key,
                    SIMILAR_LIMIT,
                )
            similar_users = await _users_by_device(conn, [item["device_key"] for item in similar_rows])

        client = parse_json(row["client"])
        return AdminDeviceDetail.model_validate(
            {
                **_item(row, []),
                "user_agent": row["user_agent"],
                "client": client if isinstance(client, dict) else {},
                "reported_at": row["reported_at"],
                "users": [
                    {
                        **_user(user),
                        "first_seen_at": user["first_seen_at"],
                        "last_seen_at": user["last_seen_at"],
                        "last_ip": user["last_ip"],
                        "requests_count": int(user["requests_count"] or 0),
                    }
                    for user in users
                ],
                "ips": [dict(ip) for ip in ips],
                "similar": [
                    {
                        **_item(item, [_user(user) for user in similar_users[item["device_key"]]]),
                    }
                    for item in similar_rows
                ],
            }
        )
