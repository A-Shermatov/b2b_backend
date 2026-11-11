"""Every change to a public profile waits for an admin.

A company's page (requisites, description, pictures, facts, categories) and a
person's name, photo and personal page are shown as they were until an admin
approves; a rejection keeps them and tells the owner why.
"""

import pytest

from tests.buyer.test_rfq_archive_avatar_telegram import PNG
from tests.sql import fetchval
from tests.test_audit_regressions import _register
from tests.test_b2b_company_features import _company

API = "/api/v1"


async def _pending_company(client, headers, company_id):
    res = await client.get(
        f"{API}/buyer/companies/{company_id}/change-requests/pending", headers=headers
    )
    assert res.status_code == 200, res.text
    return res.json()


async def _decide(client, admin, request_id, action, reason=None):
    body = {"action": action}
    if reason:
        body["reason"] = reason
    res = await client.post(
        f"{API}/admin/companies/change-requests/{request_id}/action",
        headers=admin["headers"],
        json=body,
    )
    assert res.status_code == 200, res.text
    return res.json()


@pytest.mark.asyncio
async def test_company_profile_changes_wait_for_admin(client, admin_auth):
    _, token, me = await _register(client, "buyer")
    company_id, director = await _company(client, token, me, side="buyer", title="Модерация ООО")
    before = (await client.get(f"{API}/buyer/companies/{company_id}", headers=director)).json()

    res = await client.patch(
        f"{API}/buyer/companies/{company_id}",
        headers=director,
        json={"description": "Звоните +992 900 00 00 00", "website": "https://example.tj"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # Nothing changes yet; the page says what waits for review.
    assert body["description"] == before["description"]
    assert body["website"] == before["website"]
    assert body["pending_changes"] == ["description", "website"]

    # A second save adds to the same request instead of opening another one.
    res = await client.patch(
        f"{API}/buyer/companies/{company_id}",
        headers=director,
        json={"founded_year": 2015, "industries": ["Логистика"], "category_ids": [1, 2]},
    )
    assert res.status_code == 200, res.text
    pending = await _pending_company(client, director, company_id)
    assert set(pending["proposed"]) == {
        "description",
        "website",
        "founded_year",
        "industries",
        "category_ids",
    }
    assert pending["field_labels"]["description"] == "Описание"
    count = await fetchval(
        "SELECT COUNT(*) FROM company_change_requests WHERE company_id = $1", company_id
    )
    assert count == 1

    # The admin sees it in the queue and approves: now the page shows it.
    queue = await client.get(
        f"{API}/admin/companies/change-requests", headers=admin_auth["headers"]
    )
    assert any(item["id"] == pending["id"] for item in queue.json()["items"])
    decided = await _decide(client, admin_auth, pending["id"], "approve")
    assert decided["status"] == "approved"

    after = (await client.get(f"{API}/buyer/companies/{company_id}", headers=director)).json()
    assert after["description"] == "Звоните +992 900 00 00 00"
    assert after["website"] == "https://example.tj"
    assert after["profile"]["founded_year"] == 2015
    assert after["profile"]["industries"] == ["Логистика"]
    assert sorted(c["category_id"] for c in after["categories"]) == [1, 2]
    assert after["pending_changes"] == []


@pytest.mark.asyncio
async def test_rejected_company_change_keeps_the_page_and_says_why(client, admin_auth):
    _, token, me = await _register(client, "buyer")
    company_id, director = await _company(client, token, me, side="buyer", title="Отказ ООО")
    res = await client.patch(
        f"{API}/buyer/companies/{company_id}",
        headers=director,
        json={"logo": "logos/1/new.png", "title": "Новое имя ООО"},
    )
    assert res.status_code == 200, res.text
    pending = await _pending_company(client, director, company_id)
    assert pending["media"]["logo"]["after"].endswith("logos/1/new.png")

    decided = await _decide(client, admin_auth, pending["id"], "reject", "Логотип чужой компании")
    assert decided["status"] == "rejected"
    after = (await client.get(f"{API}/buyer/companies/{company_id}", headers=director)).json()
    assert after["title"] == "Отказ ООО"
    assert after["logo"] != "logos/1/new.png"
    note = await fetchval(
        """
        SELECT body FROM notifications
        WHERE user_id = $1 ORDER BY id DESC LIMIT 1
        """,
        me["user"]["id"],
    )
    assert "Логотип чужой компании" in note


@pytest.mark.asyncio
async def test_setting_a_field_back_withdraws_it(client):
    _, token, me = await _register(client, "buyer")
    company_id, director = await _company(client, token, me, side="buyer", title="Передумали ООО")
    current = (await client.get(f"{API}/buyer/companies/{company_id}", headers=director)).json()
    await client.patch(
        f"{API}/buyer/companies/{company_id}", headers=director, json={"description": "Другое"}
    )
    res = await client.patch(
        f"{API}/buyer/companies/{company_id}",
        headers=director,
        json={"description": current["description"] or ""},
    )
    assert res.status_code == 200, res.text
    assert res.json()["pending_changes"] == []
    assert await _pending_company(client, director, company_id) is None


@pytest.mark.asyncio
async def test_name_photo_and_page_wait_for_admin(client, admin_auth):
    _, token, me = await _register(client, "both")
    auth = {"Authorization": f"Bearer {token}"}

    res = await client.patch(
        f"{API}/auth/me", headers=auth, json={"first_name": "Новое", "phone": "+992900000001"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["first_name"] == "Audit"
    assert res.json()["phone"] == "+992900000001"  # private, applies at once
    assert res.json()["pending_changes"] == ["first_name"]

    res = await client.post(
        f"{API}/auth/me/avatar", headers=auth, files={"file": ("me.png", PNG, "image/png")}
    )
    assert res.status_code == 200, res.text
    assert res.json()["avatar_url"] is None
    assert res.json()["pending_changes"] == ["avatar_url", "first_name"]

    res = await client.put(
        f"{API}/auth/me/page",
        headers=auth,
        json={"headline": "Дизайнер", "about": "Рисую логотипы", "accepts_orders": True},
    )
    assert res.status_code == 200, res.text
    assert res.json()["accepts_orders"] is True  # a switch, applies at once
    # The owner keeps seeing what they sent; the stored page is still empty.
    assert res.json()["headline"] == "Дизайнер"
    assert res.json()["pending_review"] is True
    stored = await fetchval(
        "SELECT headline FROM individual_profiles WHERE user_id = $1", me["user"]["id"]
    )
    assert stored is None

    pending = (await client.get(f"{API}/auth/me/changes", headers=auth)).json()
    assert pending["kind"] == "user"
    assert set(pending["proposed"]) == {"first_name", "avatar_url", "page"}
    assert pending["media"]["avatar_url"]["after"].endswith(".png")

    await _decide(client, admin_auth, pending["id"], "approve")
    me_after = (await client.get(f"{API}/auth/me", headers=auth)).json()
    assert me_after["user"]["first_name"] == "Новое"
    assert me_after["user"]["avatar_url"].endswith(".png")
    individual = [a for a in me_after["actors"] if a["kind"] == "individual"]
    assert individual and all(a["display_name"].startswith("Новое") for a in individual)
    page = (await client.get(f"{API}/auth/me/page", headers=auth)).json()
    assert page["headline"] == "Дизайнер"
    assert page["pending_review"] is False
    assert (await client.get(f"{API}/auth/me/changes", headers=auth)).json() is None


@pytest.mark.asyncio
async def test_removing_the_photo_is_immediate_and_drops_a_waiting_one(client):
    _, token, _me = await _register(client, "buyer")
    auth = {"Authorization": f"Bearer {token}"}
    await client.post(
        f"{API}/auth/me/avatar", headers=auth, files={"file": ("me.png", PNG, "image/png")}
    )
    res = await client.delete(f"{API}/auth/me/avatar", headers=auth)
    assert res.status_code == 200, res.text
    assert res.json()["avatar_url"] is None
    assert res.json()["pending_changes"] == []
