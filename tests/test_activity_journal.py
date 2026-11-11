"""The journal keeps every request, every opened page and the device behind them."""

import asyncio
from uuid import uuid4

import pytest

from app.core import journal as journal_module
from app.core.journal import journal
from app.core.middleware._common import safe_path
from app.utils.client_info import (
    DERIVED_PREFIX,
    device_key_for,
    fingerprint,
    is_identified,
    parse_user_agent,
)
from tests.sql import execute, fetch, fetchrow, fetchval

API = "/api/v1"

CHROME_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)
EDGE_WINDOWS = CHROME_WINDOWS + " Edg/142.0.0.0"
YANDEX = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 YaBrowser/24.7.0.0 Safari/537.36"
)
SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
SAFARI_IPAD = (
    "Mozilla/5.0 (iPad; CPU OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)
SAFARI_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"
)
CHROME_ANDROID = (
    "Mozilla/5.0 (Linux; Android 13; SM-A536B) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Mobile Safari/537.36"
)
CHROME_ANDROID_REDUCED = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Mobile Safari/537.36"
)
CUBOT_PHONE = (
    "Mozilla/5.0 (Linux; Android 11; CUBOT NOTE 20) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/119.0.0.0 Mobile Safari/537.36"
)
FIREFOX_LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


def device_headers(device: str, agent: str = CHROME_WINDOWS, visit: str | None = None) -> dict:
    headers = {"X-Device-Id": device, "User-Agent": agent}
    if visit:
        headers["X-Visit-Id"] = visit
    return headers


def new_id() -> str:
    return str(uuid4())


@pytest.mark.parametrize(
    ("agent", "hints", "expected"),
    [
        (CHROME_WINDOWS, None, ("Windows", "10/11", "Chrome", "142", "desktop", False)),
        (CHROME_WINDOWS, {"platform_version": '"15.0.0"'}, ("Windows", "11", "Chrome", "142", "desktop", False)),
        (CHROME_WINDOWS, {"platform_version": "10.0.0"}, ("Windows", "10", "Chrome", "142", "desktop", False)),
        (EDGE_WINDOWS, None, ("Windows", "10/11", "Edge", "142", "desktop", False)),
        (YANDEX, None, ("Windows", "10/11", "Яндекс Браузер", "24", "desktop", False)),
        (SAFARI_IPHONE, None, ("iOS", "17.5", "Safari", "17", "mobile", False)),
        (SAFARI_IPAD, None, ("iPadOS", "16.6", "Safari", "16", "tablet", False)),
        (SAFARI_MAC, None, ("macOS", "10.15", "Safari", "17", "desktop", False)),
        (SAFARI_MAC, {"platform_version": "14.5.0"}, ("macOS", "14.5", "Safari", "17", "desktop", False)),
        (CHROME_ANDROID, None, ("Android", "13", "Chrome", "120", "mobile", False)),
        (CHROME_ANDROID_REDUCED, {"platform_version": "14.0.0"}, ("Android", "14", "Chrome", "142", "mobile", False)),
        (CUBOT_PHONE, None, ("Android", "11", "Chrome", "119", "mobile", False)),
        (FIREFOX_LINUX, None, ("Linux", None, "Firefox", "130", "desktop", False)),
        ("curl/8.5.0", None, (None, None, "curl", "8.5", "bot", True)),
        ("python-requests/2.32.3", None, (None, None, "python-requests", "2.32", "bot", True)),
        (GOOGLEBOT, None, (None, None, "Googlebot", "2.1", "bot", True)),
        ("", None, (None, None, None, None, "other", False)),
    ],
)
def test_user_agent_is_read(agent, hints, expected):
    traits = parse_user_agent(agent, hints)
    assert (
        traits.os_name,
        traits.os_version,
        traits.browser_name,
        traits.browser_version,
        traits.device_type,
        traits.is_bot,
    ) == expected


def test_device_model_is_read_when_the_browser_names_it():
    assert parse_user_agent(CHROME_ANDROID).device_model == "SM-A536B"
    assert parse_user_agent(CUBOT_PHONE).device_model == "CUBOT NOTE 20"
    # New Chrome hides the model behind «K»; its own report still carries it.
    assert parse_user_agent(CHROME_ANDROID_REDUCED).device_model is None
    assert parse_user_agent(CHROME_ANDROID_REDUCED, {"model": '"Pixel 8"'}).device_model == "Pixel 8"


def test_device_key_is_the_browsers_own_id_or_made_from_headers():
    own = new_id()
    assert device_key_for(own.upper(), "1.2.3.4", CHROME_WINDOWS, "ru") == own
    assert is_identified(own)

    derived = device_key_for(None, "1.2.3.4", "curl/8.5.0", None)
    assert derived.startswith(DERIVED_PREFIX) and not is_identified(derived)
    # Not an id: anything else is treated as "sent none".
    assert device_key_for("<script>", "1.2.3.4", "curl/8.5.0", None) == derived
    assert device_key_for(None, "5.6.7.8", "curl/8.5.0", None) != derived


def test_fingerprint_follows_the_machine_not_the_browser_id():
    machine = {"platform": "Windows", "gpu": "ANGLE (NVIDIA RTX 3060)", "screen_width": 1920, "screen_height": 1080}
    assert fingerprint(machine) == fingerprint(dict(machine))
    assert fingerprint({**machine, "gpu": "ANGLE (Intel UHD 620)"}) != fingerprint(machine)
    # Nothing to tell machines apart by.
    assert fingerprint({"platform": "Windows"}) is None
    assert fingerprint(None) is None


def test_one_time_tokens_are_not_kept_in_a_path():
    token = "Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4"
    assert safe_path(f"/api/v1/public/invitations/{token}") == "/api/v1/public/invitations/***"
    assert safe_path("/invite/short-token") == "/invite/***"
    assert safe_path(f"/anything/{token}/more") == "/anything/***/more"
    # Object ids stay: numbers and UUIDs are not secrets.
    uuid_path = "/api/v1/buyer/rfqs/3f2b1c9e-8a4d-4f6b-9c1e-2d3a4b5c6d7e/publish"
    assert safe_path(uuid_path) == uuid_path
    assert safe_path("/api/v1/buyer/company/invitations/15") == "/api/v1/buyer/company/invitations/15"


async def _logs(client, headers, **params):
    res = await client.get(f"{API}/admin/logs", params=params, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


@pytest.mark.asyncio
async def test_a_view_is_kept_with_the_device_it_came_from(client, admin_auth, buyer_auth):
    device, visit = new_id(), new_id()
    seen = await client.get(
        f"{API}/buyer/rfqs/",
        headers={**buyer_auth["headers"], **device_headers(device, visit=visit)},
    )
    assert seen.status_code == 200, seen.text
    assert seen.headers["x-request-id"]

    logs = await _logs(client, admin_auth["headers"], source="http", kind="view", device=device)
    assert len(logs["items"]) == 1, logs
    item = logs["items"][0]
    assert (item["kind"], item["section"], item["status"]) == ("view", "rfqs", "success")
    assert item["user"]["email"] == "buyer@example.com"
    assert item["device"] == {
        "key": device,
        "identified": True,
        "type": "desktop",
        "os": "Windows 10/11",
        "browser": "Chrome 142",
        "model": None,
        "is_bot": False,
    }

    detail = await client.get(f"{API}/admin/logs/{item['id']}", headers=admin_auth["headers"])
    assert detail.status_code == 200, detail.text
    technical = detail.json()["technical"]
    assert technical["method"] == "GET"
    assert technical["path"] == f"{API}/buyer/rfqs/"
    assert technical["status_code"] == 200
    assert technical["duration_ms"] is not None
    assert technical["request_id"] == seen.headers["x-request-id"]
    assert technical["visit"] == visit
    assert detail.json()["device"]["users_count"] == 1


@pytest.mark.asyncio
async def test_the_same_view_again_is_counted_not_written_again(client, admin_auth, buyer_auth):
    device = new_id()
    headers = {**buyer_auth["headers"], **device_headers(device)}
    for _ in range(4):
        res = await client.get(f"{API}/buyer/rfqs/", params={"probe": device}, headers=headers)
        assert res.status_code == 200, res.text
    # Another search is another view.
    other = await client.get(f"{API}/buyer/rfqs/", params={"probe": "other"}, headers=headers)
    assert other.status_code == 200

    await journal.flush(everything=True)
    logs = await _logs(client, admin_auth["headers"], source="http", device=device)
    repeats = sorted(item["repeats"] for item in logs["items"])
    assert repeats == [0, 3], logs
    counted = next(item for item in logs["items"] if item["repeats"])
    assert counted["repeated_until"]
    # The device still saw every one of them.
    assert await fetchval(
        "SELECT requests_count FROM client_devices WHERE device_key = $1", device
    ) == 5


@pytest.mark.asyncio
async def test_a_refusal_is_written_at_once_and_never_folded(client, buyer_auth):
    device = new_id()
    headers = {**buyer_auth["headers"], **device_headers(device)}
    for _ in range(2):
        res = await client.get(f"{API}/admin/logs", headers=headers)
        assert res.status_code == 403
    rows = await fetch(
        "SELECT method, path, status_code, request_id FROM audit_logs WHERE device_key = $1",
        device,
    )
    assert [(row["method"], row["path"], row["status_code"]) for row in rows] == [
        ("GET", f"{API}/admin/logs", 403)
    ] * 2
    assert len({row["request_id"] for row in rows}) == 2


@pytest.mark.asyncio
async def test_pages_and_the_browsers_report_are_kept(client, admin_auth, buyer_auth):
    device, visit = new_id(), new_id()
    headers = {**buyer_auth["headers"], **device_headers(device, visit=visit)}
    view_id = new_id()
    token = "c2VjcmV0LXRva2VuLXNlY3JldC10b2tlbi1zZWNyZXQtdG9rZW4"
    sent = await client.post(
        f"{API}/activity",
        headers=headers,
        json={
            "device": {
                "platform": "Windows",
                "platform_version": "15.0.0",
                "architecture": "x86",
                "bitness": "64",
                "brands": ["Chromium 142", "Google Chrome 142"],
                "languages": ["ru-RU", "ru", "en"],
                "timezone": "Asia/Dushanbe",
                "timezone_offset": 300,
                "screen_width": 1920,
                "screen_height": 1080,
                "pixel_ratio": 1.25,
                "color_depth": 24,
                "cpu_cores": 8,
                "memory_gb": 8,
                "touch_points": 0,
                "gpu": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11)",
                "cookies": True,
                "webdriver": False,
                "unknown_field": "ignored",
            },
            "events": [
                {
                    "type": "view",
                    "id": view_id,
                    "path": "/customer/rfqs/12",
                    "query": {"tab": "offers"},
                    "title": "Заявка № 12",
                    "referrer": "/customer/rfqs",
                    "ago_ms": 1500,
                },
                {"type": "view", "id": new_id(), "path": "/reset-password", "query": {"token": token}},
                {"type": "view", "id": new_id(), "path": f"/invite/{token}"},
                {"type": "view", "id": new_id(), "path": "/register", "query": {"invite": token}},
                # Not a page of the site.
                {"type": "view", "id": new_id(), "path": "https://example.org/elsewhere"},
                {"type": "view", "id": new_id(), "path": "//example.org"},
            ],
        },
    )
    assert sent.status_code == 200, sent.text
    assert sent.json() == {"accepted": 4}

    left = await client.post(
        f"{API}/activity",
        headers=headers,
        json={"events": [{"type": "leave", "id": view_id, "seconds": 42, "active_seconds": 30}]},
    )
    assert left.status_code == 200, left.text

    pages = await _logs(client, admin_auth["headers"], source="pages", device=device)
    by_path = {item["path"]: item for item in pages["items"]}
    assert set(by_path) == {"/customer/rfqs/12", "/reset-password", "/invite/***", "/register"}
    opened = by_path["/customer/rfqs/12"]
    assert opened["source"] == "page"
    assert opened["title"] == "Заявка № 12"
    assert opened["query"] == {"tab": "offers"}
    assert opened["referrer"] == "/customer/rfqs"
    assert (opened["seconds"], opened["active_seconds"]) == (42, 30)
    assert opened["object_id"] == "12"
    assert opened["user"]["email"] == "buyer@example.com"
    assert by_path["/reset-password"]["query"] == {"token": "***"}
    assert by_path["/register"]["query"] == {"invite": "***"}
    assert by_path["/reset-password"]["seconds"] is None
    # The token is nowhere in what was kept.
    assert not await fetchval(
        "SELECT COUNT(*) FROM audit_logs WHERE device_key = $1 AND (path LIKE $2 OR details::text LIKE $2)",
        device,
        f"%{token}%",
    )
    # The report itself is not a row of the journal.
    assert not await fetchval(
        "SELECT COUNT(*) FROM audit_logs WHERE device_key = $1 AND path = $2",
        device,
        f"{API}/activity",
    )

    detail = await client.get(f"{API}/admin/logs/{opened['id']}", headers=admin_auth["headers"])
    assert detail.status_code == 200, detail.text
    assert detail.json()["technical"]["visit"] == visit
    assert detail.json()["device"]["screen"] == "1920×1080 @1.25x"

    card = await client.get(f"{API}/admin/devices/{device}", headers=admin_auth["headers"])
    assert card.status_code == 200, card.text
    card = card.json()
    # Only the browser's own report tells Windows 11 from 10.
    assert (card["os"], card["browser"], card["type"]) == ("Windows 11", "Chrome 142", "desktop")
    assert (card["language"], card["timezone"], card["screen"]) == ("ru-RU", "Asia/Dushanbe", "1920×1080 @1.25x")
    assert card["client"]["gpu"].startswith("ANGLE (NVIDIA")
    assert card["client"]["cpu_cores"] == 8
    assert "unknown_field" not in card["client"]
    assert card["identified"] is True and card["automated"] is False
    assert [user["email"] for user in card["users"]] == ["buyer@example.com"]
    assert card["ips"] and card["ips"][0]["requests_count"] >= 1

    # A later request does not put «10/11» back.
    await client.get(f"{API}/buyer/rfqs/", headers=headers)
    again = await client.get(f"{API}/admin/devices/{device}", headers=admin_auth["headers"])
    assert again.json()["os"] == "Windows 11"


@pytest.mark.asyncio
async def test_a_guest_has_a_device_too(client, admin_auth):
    device = new_id()
    sent = await client.post(
        f"{API}/activity",
        headers=device_headers(device, agent=SAFARI_IPHONE),
        json={"events": [{"type": "view", "id": new_id(), "path": "/services"}]},
    )
    assert sent.status_code == 200, sent.text
    assert sent.json() == {"accepted": 1}

    pages = await _logs(client, admin_auth["headers"], source="pages", device=device)
    assert len(pages["items"]) == 1
    item = pages["items"][0]
    assert item["user"] is None and item["audience"] == "users"
    assert (item["device"]["os"], item["device"]["browser"], item["device"]["type"]) == (
        "iOS 17.5",
        "Safari 17",
        "mobile",
    )


@pytest.mark.asyncio
async def test_a_report_with_junk_in_it_is_still_taken(client):
    device = new_id()
    sent = await client.post(
        f"{API}/activity",
        headers=device_headers(device),
        json={
            "device": {
                "platform": "W" * 5000,
                "screen_width": "wide",
                "screen_height": -5,
                "cpu_cores": 10**9,
                "memory_gb": "NaN",
                "mobile": "yes",
                "languages": "ru",
                "brands": [1, None, "B" * 500],
                "timezone": "Asia/Dushanbe\x00\x1b[31m",
            },
            "events": [{"type": "view", "id": new_id(), "path": "/" + "p" * 2000, "title": 5}],
        },
    )
    assert sent.status_code == 200, sent.text
    row = await fetchrow("SELECT client, timezone, screen FROM client_devices WHERE device_key = $1", device)
    assert row["timezone"] == "Asia/Dushanbe[31m"
    assert row["screen"] is None
    assert len(await fetchval("SELECT path FROM audit_logs WHERE device_key = $1", device)) <= 500

    too_many = await client.post(
        f"{API}/activity",
        headers=device_headers(device),
        json={"events": [{"type": "view", "id": new_id(), "path": "/"} for _ in range(21)]},
    )
    assert too_many.status_code == 422


@pytest.mark.asyncio
async def test_one_browser_cannot_flood_the_journal_with_reports(client, monkeypatch):
    device = new_id()
    asked: list[tuple[str, int]] = []

    async def over_the_device_limit(key, limit, window_seconds=60):
        asked.append((key, limit))
        return not key.startswith("activity:device:")

    monkeypatch.setattr("app.utils.redis_client.check_rate_limit", over_the_device_limit)
    refused = await client.post(
        f"{API}/activity",
        headers=device_headers(device),
        json={"events": [{"type": "view", "id": new_id(), "path": "/services"}]},
    )
    assert refused.status_code == 429
    assert (f"activity:device:{device}", 30) in asked
    assert not await fetchval("SELECT COUNT(*) FROM audit_logs WHERE device_key = $1 AND action = 'page.view'", device)


@pytest.mark.asyncio
async def test_a_view_can_only_be_ended_by_the_device_that_opened_it(client, buyer_auth):
    device, stranger, view_id = new_id(), new_id(), new_id()
    await client.post(
        f"{API}/activity",
        headers={**buyer_auth["headers"], **device_headers(device)},
        json={"events": [{"type": "view", "id": view_id, "path": "/customer"}]},
    )
    await client.post(
        f"{API}/activity",
        headers=device_headers(stranger),
        json={"events": [{"type": "leave", "id": view_id, "seconds": 999}]},
    )
    assert await fetchval("SELECT duration_ms FROM audit_logs WHERE request_id = $1", view_id) is None


@pytest.mark.asyncio
async def test_one_device_several_accounts_and_a_sign_in_from_a_new_one(client, admin_auth):
    shared, fresh = new_id(), new_id()

    async def sign_in(email: str, password: str, device: str):
        res = await client.post(
            f"{API}/auth/login",
            json={"email": email, "password": password},
            headers=device_headers(device),
        )
        assert res.status_code == 200, res.text
        await journal.flush()

    await sign_in("buyer@example.com", "Buyer123!", shared)
    await sign_in("supplier@example.com", "Supplier123!", shared)

    devices = await client.get(
        f"{API}/admin/devices",
        params={"flag": "shared", "query": shared},
        headers=admin_auth["headers"],
    )
    assert devices.status_code == 200, devices.text
    found = devices.json()["items"]
    assert len(found) == 1 and found[0]["users_count"] == 2
    assert {user["email"] for user in found[0]["users"]} == {"buyer@example.com", "supplier@example.com"}

    buyer_id = await fetchval("SELECT id FROM users WHERE email = 'buyer@example.com'")
    mine = await client.get(
        f"{API}/admin/devices", params={"user_id": buyer_id, "query": shared}, headers=admin_auth["headers"]
    )
    assert [item["key"] for item in mine.json()["items"]] == [shared]

    # The buyer has used `shared`; `fresh` is new for them.
    notices_before = await fetchval(
        "SELECT COUNT(*) FROM notifications WHERE user_id = $1 AND title = 'Вход с нового устройства'",
        buyer_id,
    )
    await sign_in("buyer@example.com", "Buyer123!", fresh)
    first = await _logs(client, admin_auth["headers"], source="events", action="login", device=fresh)
    assert [item["details"].get("new_device") for item in first["items"]] == [True]
    assert first["items"][0]["device"]["key"] == fresh
    # The owner of the account is told, with what the device is.
    await asyncio.gather(*list(journal_module._background))
    notice = await fetchrow(
        """
        SELECT body FROM notifications
        WHERE user_id = $1 AND title = 'Вход с нового устройства'
        ORDER BY id DESC LIMIT 1
        """,
        buyer_id,
    )
    assert "Windows 10/11 · Chrome 142" in notice["body"]

    await sign_in("buyer@example.com", "Buyer123!", fresh)
    second = await _logs(client, admin_auth["headers"], source="events", action="login", device=fresh)
    assert [item["details"].get("new_device") for item in second["items"]] == [None, True]
    # A device already used is not news.
    await asyncio.gather(*list(journal_module._background))
    assert (
        await fetchval(
            "SELECT COUNT(*) FROM notifications WHERE user_id = $1 AND title = 'Вход с нового устройства'",
            buyer_id,
        )
        == notices_before + 1
    )


@pytest.mark.asyncio
async def test_a_script_without_an_id_is_told_apart_by_its_headers(client, admin_auth):
    agent = f"curl/8.5.{uuid4().int % 1000}"
    res = await client.get(f"{API}/catalog/categories", headers={"User-Agent": agent})
    assert res.status_code < 500

    devices = await client.get(
        f"{API}/admin/devices",
        params={"flag": "unidentified", "query": agent},
        headers=admin_auth["headers"],
    )
    assert devices.status_code == 200, devices.text
    found = devices.json()["items"]
    assert len(found) == 1
    assert found[0]["identified"] is False
    assert (found[0]["type"], found[0]["is_bot"], found[0]["browser"]) == ("bot", True, "curl 8.5")
    assert found[0]["key"].startswith(DERIVED_PREFIX)


@pytest.mark.asyncio
async def test_a_flood_is_one_row_with_a_counter(client, admin_auth, monkeypatch):
    device = new_id()

    async def refuse(*_args, **_kwargs):
        return False

    async def allow(*_args, **_kwargs):
        return True

    monkeypatch.setattr("app.core.middleware.rate_limit.check_rate_limit", refuse)
    for _ in range(5):
        res = await client.get(f"{API}/catalog/categories", headers=device_headers(device))
        assert res.status_code == 429
    monkeypatch.setattr("app.core.middleware.rate_limit.check_rate_limit", allow)

    await journal.flush(everything=True)
    logs = await _logs(
        client, admin_auth["headers"], source="http", status="rate_limited", device=device
    )
    assert len(logs["items"]) == 1, logs
    assert logs["items"][0]["repeats"] == 4


@pytest.mark.asyncio
async def test_devices_and_pages_need_the_journal_permission(client, buyer_auth):
    for path in ("/admin/devices", f"/admin/devices/{new_id()}", "/admin/logs?source=pages"):
        res = await client.get(f"{API}{path}", headers=buyer_auth["headers"])
        assert res.status_code == 403, path


@pytest.mark.asyncio
async def test_old_views_are_removed_and_everything_else_stays(client):
    marker = f"cleanup-{uuid4().hex[:10]}"

    async def add(action: str, status_code: int | None, age_days: int) -> None:
        await execute(
            """
            INSERT INTO audit_logs (action, resource_type, resource_id, status_code, created_at)
            VALUES ($1, 'http', $2, $3, NOW() - make_interval(days => $4))
            """,
            action,
            marker,
            status_code,
            age_days,
        )

    await add("http.get", 200, 400)  # an old view: goes
    await add("page.view", None, 400)  # an old page: goes
    await add("http.get", 403, 400)  # a refusal stays
    await add("http.post", 200, 400)  # a change stays
    await add("rfq.create", None, 400)  # an event stays
    await add("http.get", 200, 10)  # a recent view stays

    assert await journal.cleanup() >= 2
    left = await fetch(
        "SELECT action, status_code FROM audit_logs WHERE resource_id = $1 ORDER BY id", marker
    )
    assert [(row["action"], row["status_code"]) for row in left] == [
        ("http.get", 403),
        ("http.post", 200),
        ("rfq.create", None),
        ("http.get", 200),
    ]
