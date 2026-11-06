import json

import pytest

from app.modules.notifications.ws_manager import notification_ws_manager

API = "/api/v1"


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed_with: int | None = None

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


async def _user_id(client, admin_auth, email: str) -> int:
    res = await client.get(f"{API}/admin/users", params={"query": email}, headers=admin_auth["headers"])
    return next(u["id"] for u in res.json()["items"] if u["email"] == email)


@pytest.mark.asyncio
async def test_revoking_sessions_signs_the_user_out_at_once(client, admin_auth, supplier_auth, buyer_auth):
    supplier_id = await _user_id(client, admin_auth, "supplier@example.com")
    buyer_id = await _user_id(client, admin_auth, "buyer@example.com")
    supplier_tab, buyer_tab = FakeSocket(), FakeSocket()
    await notification_ws_manager.connect(supplier_id, supplier_tab)
    await notification_ws_manager.connect(buyer_id, buyer_tab)
    try:
        res = await client.post(f"{API}/admin/users/{supplier_id}/sessions/revoke", headers=admin_auth["headers"])
        assert res.status_code == 200, res.text

        assert supplier_tab.sent[-1]["event"] == "session_ended"
        assert supplier_tab.sent[-1]["data"]["reason"] == "revoked"
        assert supplier_tab.closed_with == 4401
        assert buyer_tab.sent == [] and buyer_tab.closed_with is None

        # The old token no longer works anywhere.
        me = await client.get(f"{API}/auth/me", headers=supplier_auth["headers"])
        assert me.status_code == 401
    finally:
        notification_ws_manager.disconnect(buyer_id, buyer_tab)


@pytest.mark.asyncio
async def test_blocking_signs_the_user_out_with_the_reason(client, admin_auth):
    supplier_id = await _user_id(client, admin_auth, "supplier@example.com")
    tab = FakeSocket()
    await notification_ws_manager.connect(supplier_id, tab)
    try:
        res = await client.patch(
            f"{API}/admin/users/{supplier_id}/status",
            json={"status": "blocked", "reason": "Нарушение правил", "duration_days": 1},
            headers=admin_auth["headers"],
        )
        assert res.status_code == 200, res.text
        event = tab.sent[-1]
        assert event["event"] == "session_ended"
        assert event["data"]["reason"] == "blocked"
        assert "Нарушение правил" in event["data"]["message"]
        assert tab.closed_with == 4401
    finally:
        await client.patch(
            f"{API}/admin/users/{supplier_id}/status",
            json={"status": "active"},
            headers=admin_auth["headers"],
        )
