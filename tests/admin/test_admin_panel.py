"""The admin panel: the counters of its menu, what a moderator may do by default,
and whose message is whose in a chat between two companies."""

import pytest

from app.db.schema import prepare_database
from tests.buyer.test_contract_messages import _create_contract
from app.modules.roles.services.common import keep_what_a_box_held
from tests.sql import fetch, fetchval, upsert_moderator
from tests.staff import create_role_with_permissions, permission_ids, unique_suffix

API = "/api/v1"


async def _moderator_headers(client) -> dict:
    await upsert_moderator()
    res = await client.post(
        f"{API}/auth/login", json={"email": "moderator@example.com", "password": "Moderator123!"}
    )
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


@pytest.mark.asyncio
async def test_the_menu_counts_each_queue_on_its_own(client, admin_auth):
    res = await client.get(f"{API}/admin/nav-counts", headers=admin_auth["headers"])
    assert res.status_code == 200, res.text
    counts = res.json()
    # Goods waiting for approval and complaints are two pages of the menu.
    assert counts["catalog"] == await fetchval(
        "SELECT COUNT(*) FROM catalog_items WHERE status::text = 'pending_review'"
    )
    assert counts["moderation"] == counts["catalog"] + counts["reports"]
    # Changes of a checked profile wait on the page of client checks.
    clients = await fetchval(
        """
        SELECT
          (SELECT COUNT(*) FROM companies
           WHERE verification_status::text IN ('pending', 'needs_documents'))
        + (SELECT COUNT(*) FROM users
           WHERE individual_verification_status IN ('pending', 'needs_documents')
             AND role::text NOT IN ('admin', 'superadmin', 'moderator'))
        """
    )
    changes = await fetchval("SELECT COUNT(*) FROM company_change_requests WHERE status = 'pending'")
    assert counts["companies"] == clients + changes


@pytest.mark.asyncio
async def test_a_moderator_checks_but_has_no_money_staff_or_journal(client):
    headers = await _moderator_headers(client)
    for path in ("/admin/companies", "/admin/disputes", "/admin/users", "/admin/verification/counts"):
        res = await client.get(f"{API}{path}", headers=headers)
        assert res.status_code == 200, f"{path}: {res.text}"
    for path in ("/admin/finance", "/admin/staff", "/admin/roles", "/admin/logs", "/admin/devices"):
        res = await client.get(f"{API}{path}", headers=headers)
        assert res.status_code == 403, f"{path}: {res.status_code}"


@pytest.mark.asyncio
async def test_a_right_given_back_to_the_moderator_stays_after_a_restart(client, admin_auth):
    moderator = await _moderator_headers(client)
    role_id = await fetchval("SELECT id FROM roles WHERE name = 'moderator'")
    matrix = await client.get(f"{API}/admin/roles/{role_id}/matrix", headers=admin_auth["headers"])
    assert matrix.status_code == 200, matrix.text
    before = [
        cell["permission_id"]
        for row in matrix.json()["rows"]
        for cell in (row["read"], row["write"], row["delete"])
        if cell["enabled"] and cell["permission_id"]
    ]
    money = {row["resource"]: row for row in matrix.json()["rows"]}["admin_finance"]
    assert money["read"]["enabled"] is False
    rights_before = await _active_rights(role_id)
    assert "admin_companies.update" in rights_before and "admin_companies.block" not in rights_before

    try:
        saved = await client.put(
            f"{API}/admin/roles/{role_id}/matrix",
            json={"permission_ids": [*before, *await permission_ids("admin_finance.view")]},
            headers=admin_auth["headers"],
        )
        assert saved.status_code == 200, saved.text
        assert (await client.get(f"{API}/admin/finance", headers=moderator)).status_code == 200

        # A box that was ticked before keeps what it held: saving the table for
        # money does not let the moderator block companies or close deals by force.
        rows = {row["resource"]: row for row in saved.json()["rows"]}
        assert rows["admin_companies"]["write"]["enabled"] is True
        assert "блокировка" in rows["admin_companies"]["write"]["missing"]
        assert "принудительное завершение" in rows["admin_contracts"]["write"]["missing"]
        assert rows["admin_finance"]["read"] == {**rows["admin_finance"]["read"], "enabled": True, "missing": []}
        assert await _active_rights(role_id) == rights_before | {"admin_finance.view"}

        # The API starts again: the defaults are applied to the database once, not every time.
        await prepare_database()
        assert (await client.get(f"{API}/admin/finance", headers=moderator)).status_code == 200
        # What was not given back is still closed.
        assert (await client.get(f"{API}/admin/staff", headers=moderator)).status_code == 403
    finally:
        restored = await client.put(
            f"{API}/admin/roles/{role_id}/matrix",
            json={"permission_ids": before},
            headers=admin_auth["headers"],
        )
        assert restored.status_code == 200, restored.text
    assert (await client.get(f"{API}/admin/finance", headers=moderator)).status_code == 403
    assert await _active_rights(role_id) == rights_before


@pytest.mark.asyncio
async def test_a_newly_ticked_box_gives_all_its_actions(client, admin_auth):
    role_id = await create_role_with_permissions(
        client, admin_auth["headers"], f"checker-{unique_suffix()}", ["admin_companies.view"]
    )
    assert await _active_rights(role_id) == {"admin_companies.view"}

    saved = await client.put(
        f"{API}/admin/roles/{role_id}/matrix",
        json={"permission_ids": await permission_ids("admin_companies.update")},
        headers=admin_auth["headers"],
    )
    assert saved.status_code == 200, saved.text
    # «Изменение», ticked for the first time, is every action of the section but deletion.
    assert {"admin_companies.view", "admin_companies.update", "admin_companies.block"} <= await _active_rights(role_id)
    companies = {row["resource"]: row for row in saved.json()["rows"]}["admin_companies"]
    assert companies["write"] == {**companies["write"], "enabled": True, "missing": []}


def test_which_rights_a_saved_table_gives():
    rights = {
        1: {"resource": "admin_companies", "action": "view"},
        2: {"resource": "admin_companies", "action": "update"},
        3: {"resource": "admin_companies", "action": "block"},
        4: {"resource": "admin_finance", "action": "view"},
        5: {"resource": "admin_finance", "action": "update"},
    }
    # Held before and still ticked: stays as it was, without «block».
    assert keep_what_a_box_held({1, 2}, {1, 2}, rights) == {1, 2}
    # Ticked for the first time: everything of the box, and the right to open the section.
    assert keep_what_a_box_held({1, 2, 5}, {1, 2}, rights) == {1, 2, 4, 5}
    assert keep_what_a_box_held({3}, set(), rights) == {1, 2, 3}
    # Unticked: gone, whatever it held.
    assert keep_what_a_box_held({1}, {1, 2, 3}, rights) == {1}
    assert keep_what_a_box_held(set(), {1, 2}, rights) == set()


async def _active_rights(role_id: int) -> set[str]:
    rows = await fetch(
        """
        SELECT p.name FROM role_permissions rp
        JOIN permissions p ON p.id = rp.permission_id
        WHERE rp.role_id = $1 AND rp.is_active = TRUE
        """,
        role_id,
    )
    return {row["name"] for row in rows}


def _as_company(auth: dict, side: str) -> dict:
    """The person's headers for acting as their company on this side."""
    actor = next(
        (a for a in auth["me"]["actors"] if a.get("side") == side and a.get("company_id")),
        None,
    )
    if actor is None:
        pytest.skip(f"No company profile on the {side} side")
    return {**auth, "actor_id": actor["id"], "headers": {**auth["headers"], "X-Actor-Id": str(actor["id"])}}


@pytest.mark.asyncio
async def test_a_companys_message_is_its_own_not_the_moderators(client, admin_auth, buyer_auth, supplier_auth):
    buyer = _as_company(buyer_auth, "buyer")
    supplier = _as_company(supplier_auth, "supplier")
    title = f"Chat sides {unique_suffix()}"
    contract_id = await _create_contract(client, buyer, supplier, title)

    asked = await client.post(
        f"{API}/buyer/contracts/{contract_id}/messages", headers=buyer["headers"], json={"text": "Когда начнёте?"}
    )
    assert asked.status_code == 200, asked.text
    answered = await client.post(
        f"{API}/supplier/contracts/{contract_id}/messages", headers=supplier["headers"], json={"text": "Завтра."}
    )
    assert answered.status_code == 200, answered.text

    chats = await client.get(f"{API}/admin/chats", params={"query": title}, headers=admin_auth["headers"])
    assert chats.status_code == 200, chats.text
    chat_id = next(item["id"] for item in chats.json()["items"] if item["kind"] == "contract")
    noted = await client.post(
        f"{API}/admin/chats/{chat_id}/messages", headers=admin_auth["headers"], json={"text": "Модерация на связи."}
    )
    assert noted.status_code == 200, noted.text

    chat = await client.get(f"{API}/admin/chats/{chat_id}", headers=admin_auth["headers"])
    assert chat.status_code == 200, chat.text
    by_text = {message["text"]: message for message in chat.json()["messages"]}
    # A company writes through its people: their messages belong to their side.
    assert by_text["Когда начнёте?"]["side"] == "buyer"
    assert by_text["Завтра."]["side"] == "supplier"
    assert not by_text["Когда начнёте?"]["sender_name"].startswith("Модератор")
    # Only the staff of the platform are the moderator, and the panel says which one.
    assert by_text["Модерация на связи."]["side"] == "admin"
    assert by_text["Модерация на связи."]["sender_name"].startswith("Модератор · ")
