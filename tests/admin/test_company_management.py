import json
from io import BytesIO

import pytest

from app.core.security_core import hash_password
from tests.sql import execute, fetch, fetchrow, fetchval, upsert_staff_user
from tests.staff import (
    API,
    create_role_with_permissions,
    create_staff,
    login_headers,
    unique_suffix,
)


async def _create_user(email: str, role: str = "buyer") -> int:
    return await fetchval(
        """
        INSERT INTO users (
            email, password_hash, first_name, last_name, role, role_id, status,
            created_at, updated_at
        )
        VALUES (
            $1, $2, 'Test', 'Member', $3::user_role,
            (SELECT id FROM roles WHERE name = $4),
            'active'::user_status, NOW(), NOW()
        )
        RETURNING id
        """,
        email,
        hash_password("Member12345!"),
        role,
        role,
    )


async def _create_company(*, side: str = "buyer", verification: str = "pending") -> dict:
    suffix = unique_suffix()
    owner_email = f"owner-{suffix}@example.com"
    owner_id = await _create_user(owner_email, role=side)
    title = f"Mgmt Co {suffix}"
    company_id = await fetchval(
        """
        INSERT INTO companies (
            title, actor_type, owner_id, legal_name, tax_number, city, country,
            verification_status, operational_status, rating, created_at, updated_at
        )
        VALUES (
            $1, $2::actor_type, $3, 'ООО Тест', '123456789', 'Душанбе', 'Таджикистан',
            $4::verification_status, 'active'::company_operational_status, 0, NOW(), NOW()
        )
        RETURNING id
        """,
        title,
        side,
        owner_id,
        verification,
    )
    await execute(
        "INSERT INTO company_users (company_id, user_id, role) VALUES ($1, $2, 'director')",
        company_id,
        owner_id,
    )
    actor_id = await fetchval(
        """
        INSERT INTO actors (
            kind, side, user_id, company_id, display_name, trust_level, is_active,
            created_at, updated_at
        )
        VALUES (
            'company', $1::actor_side, NULL, $2, $3,
            $4::trust_level, TRUE, NOW(), NOW()
        )
        RETURNING id
        """,
        side,
        company_id,
        title,
        "verified" if verification == "verified" else "standard",
    )
    return {
        "id": company_id,
        "title": title,
        "owner_id": owner_id,
        "owner_email": owner_email,
        "actor_id": actor_id,
        "suffix": suffix,
    }


async def _last_audit(company_id: int, action_prefix: str):
    row = await fetchrow(
        """
        SELECT action, details FROM audit_logs
        WHERE resource_type = 'company' AND resource_id = $1 AND action LIKE $2
        ORDER BY id DESC LIMIT 1
        """,
        str(company_id),
        f"{action_prefix}%",
    )
    if row is None:
        return None
    details = row["details"]
    if isinstance(details, str):
        details = json.loads(details)
    return {"action": row["action"], "details": details}


@pytest.fixture
def fake_upload(monkeypatch):
    from app.utils.storage import storage_service

    uploads: list[str] = []

    async def _upload(file, prefix: str = "uploads"):
        await file.read()
        key = f"{prefix}/test-{len(uploads)}-{file.filename}"
        uploads.append(key)
        return key, f"http://test/{key}"

    monkeypatch.setattr(storage_service, "upload", _upload)
    return uploads


@pytest.mark.asyncio
async def test_list_companies_filters_sort_and_counts(client, admin_auth):
    buyer_company = await _create_company(side="buyer")
    supplier_company = await _create_company(side="supplier", verification="verified")
    headers = admin_auth["headers"]

    res = await client.get(
        f"{API}/admin/companies",
        params={"actor_type": "buyer", "query": buyer_company["title"]},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert [item["id"] for item in items] == [buyer_company["id"]]
    assert items[0]["members_count"] == 1
    assert items[0]["products_count"] == 0
    assert items[0]["contracts_count"] == 0

    res = await client.get(
        f"{API}/admin/companies",
        params={"actor_type": "buyer", "query": supplier_company["title"]},
        headers=headers,
    )
    assert res.json()["items"] == []

    res = await client.get(
        f"{API}/admin/companies",
        params={
            "verification_status": "verified",
            "operational_status": "active",
            "vip": "false",
            "query": supplier_company["title"],
        },
        headers=headers,
    )
    assert [item["id"] for item in res.json()["items"]] == [supplier_company["id"]]

    res = await client.get(
        f"{API}/admin/companies",
        params={"query": "Mgmt Co", "sort": "title", "order": "asc", "page_size": 100},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    titles = [item["title"] for item in res.json()["items"]]
    assert titles == sorted(titles, key=str.lower)


@pytest.mark.asyncio
async def test_admin_updates_company_fields_and_logo(client, admin_auth, fake_upload):
    company = await _create_company()
    headers = admin_auth["headers"]

    res = await client.patch(
        f"{API}/admin/companies/{company['id']}",
        json={
            "title": f"Renamed {company['suffix']}",
            "city": "Худжанд",
            "website": "",
            "founded_year": 2001,
            "industries": ["Логистика"],
        },
        headers=headers,
    )
    assert res.status_code == 200, res.text
    detail = res.json()
    assert detail["title"] == f"Renamed {company['suffix']}"
    assert detail["city"] == "Худжанд"
    assert detail["website"] is None
    assert detail["profile"]["founded_year"] == 2001
    assert detail["profile"]["industries"] == ["Логистика"]

    display_name = await fetchval("SELECT display_name FROM actors WHERE id = $1", company["actor_id"])
    assert display_name == f"Renamed {company['suffix']}"

    audit = await _last_audit(company["id"], "admin.company.update")
    assert audit["details"]["changes"]["city"] == {"before": "Душанбе", "after": "Худжанд"}
    assert audit["details"]["changes"]["title"]["before"] == company["title"]
    assert "profile.founded_year" in audit["details"]["changes"]

    logo_res = await client.post(
        f"{API}/admin/companies/{company['id']}/logo",
        files={"file": ("logo.png", BytesIO(b"\x89PNG test"), "image/png")},
        headers=headers,
    )
    assert logo_res.status_code == 200, logo_res.text
    assert logo_res.json()["logo"] == fake_upload[-1]
    assert fake_upload[-1].startswith(f"logos/{company['owner_id']}/")
    stored = await fetchval("SELECT logo FROM companies WHERE id = $1", company["id"])
    assert stored == fake_upload[-1]


@pytest.mark.asyncio
async def test_admin_manages_members_and_cannot_remove_owner(client, admin_auth):
    company = await _create_company()
    headers = admin_auth["headers"]
    member_email = f"member-{company['suffix']}@example.com"
    member_id = await _create_user(member_email)

    add_res = await client.post(
        f"{API}/admin/companies/{company['id']}/members",
        json={"email": member_email.upper(), "role": "accountant"},
        headers=headers,
    )
    assert add_res.status_code == 200, add_res.text
    members = {m["user_id"]: m for m in add_res.json()["members"]}
    assert members[member_id]["role"] == "accountant"
    assert members[company["owner_id"]]["is_owner"] is True
    assert (await _last_audit(company["id"], "admin.company.member.add"))["action"] == (
        "admin.company.member.add"
    )

    duplicate = await client.post(
        f"{API}/admin/companies/{company['id']}/members",
        json={"email": member_email, "role": "admin"},
        headers=headers,
    )
    assert duplicate.status_code == 409

    staff_email = f"staff-member-{company['suffix']}@example.com"
    await upsert_staff_user(email=staff_email, password="Staff12345!", role_name="moderator")
    staff_add = await client.post(
        f"{API}/admin/companies/{company['id']}/members",
        json={"email": staff_email, "role": "admin"},
        headers=headers,
    )
    assert staff_add.status_code == 422

    director = await client.post(
        f"{API}/admin/companies/{company['id']}/members",
        json={"email": member_email, "role": "director"},
        headers=headers,
    )
    assert director.status_code == 422

    update_res = await client.patch(
        f"{API}/admin/companies/{company['id']}/members/{member_id}",
        json={"role": "moderator"},
        headers=headers,
    )
    assert update_res.status_code == 200, update_res.text
    role = await fetchval(
        "SELECT role::text FROM company_users WHERE company_id = $1 AND user_id = $2",
        company["id"],
        member_id,
    )
    assert role == "moderator"
    audit = await _last_audit(company["id"], "admin.company.member.update")
    assert audit["details"]["changes"]["role"] == {"before": "accountant", "after": "moderator"}

    owner_role = await client.patch(
        f"{API}/admin/companies/{company['id']}/members/{company['owner_id']}",
        json={"role": "admin"},
        headers=headers,
    )
    assert owner_role.status_code == 403

    remove_owner = await client.delete(
        f"{API}/admin/companies/{company['id']}/members/{company['owner_id']}",
        headers=headers,
    )
    assert remove_owner.status_code == 403

    remove_res = await client.delete(
        f"{API}/admin/companies/{company['id']}/members/{member_id}",
        headers=headers,
    )
    assert remove_res.status_code == 200, remove_res.text
    assert member_id not in {m["user_id"] for m in remove_res.json()["members"]}
    assert (await _last_audit(company["id"], "admin.company.member.remove")) is not None


@pytest.mark.asyncio
async def test_admin_changes_company_owner(client, admin_auth):
    company = await _create_company(side="supplier")
    headers = admin_auth["headers"]
    new_owner_email = f"new-owner-{company['suffix']}@example.com"
    new_owner_id = await _create_user(new_owner_email, role="buyer")

    same = await client.post(
        f"{API}/admin/companies/{company['id']}/owner",
        json={"user_id": company["owner_id"]},
        headers=headers,
    )
    assert same.status_code == 409

    res = await client.post(
        f"{API}/admin/companies/{company['id']}/owner",
        json={"email": new_owner_email, "reason": "Смена директора"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    detail = res.json()
    assert detail["owner"]["id"] == new_owner_id

    roles = {
        row["user_id"]: row["role"]
        for row in await fetch(
            "SELECT user_id, role::text AS role FROM company_users WHERE company_id = $1",
            company["id"],
        )
    }
    assert roles[new_owner_id] == "director"
    assert roles[company["owner_id"]] == "admin"
    assert await fetchval("SELECT owner_id FROM companies WHERE id = $1", company["id"]) == new_owner_id

    # Company actors are keyed by company, not by owner: the same actor stays in place.
    actor = await fetchrow(
        "SELECT user_id, company_id, is_active FROM actors WHERE id = $1", company["actor_id"]
    )
    assert actor["user_id"] is None
    assert actor["company_id"] == company["id"]
    assert actor["is_active"] is True
    # New owner gains the platform side required to act for the supplier company.
    assert await fetchval("SELECT role::text FROM users WHERE id = $1", new_owner_id) == "both"

    audit = await _last_audit(company["id"], "admin.company.owner.change")
    assert audit["details"]["changes"]["owner_id"] == {
        "before": company["owner_id"],
        "after": new_owner_id,
    }
    assert audit["details"]["reason"] == "Смена директора"

    remove_new_owner = await client.delete(
        f"{API}/admin/companies/{company['id']}/members/{new_owner_id}",
        headers=headers,
    )
    assert remove_new_owner.status_code == 403
    remove_old_owner = await client.delete(
        f"{API}/admin/companies/{company['id']}/members/{company['owner_id']}",
        headers=headers,
    )
    assert remove_old_owner.status_code == 200, remove_old_owner.text


@pytest.mark.asyncio
async def test_admin_sets_company_categories(client, admin_auth):
    company = await _create_company()
    headers = admin_auth["headers"]
    category_ids = [row["id"] for row in await fetch("SELECT id FROM categories ORDER BY id LIMIT 2")]
    assert len(category_ids) == 2

    res = await client.put(
        f"{API}/admin/companies/{company['id']}/categories",
        json={"category_ids": category_ids},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    assert sorted(cat["id"] for cat in res.json()["categories"]) == category_ids
    audit = await _last_audit(company["id"], "admin.company.categories.update")
    assert audit["details"]["changes"]["category_ids"] == {"before": [], "after": category_ids}

    res = await client.put(
        f"{API}/admin/companies/{company['id']}/categories",
        json={"category_ids": [category_ids[0]]},
        headers=headers,
    )
    assert res.status_code == 200
    assert [cat["id"] for cat in res.json()["categories"]] == [category_ids[0]]

    bad = await client.put(
        f"{API}/admin/companies/{company['id']}/categories",
        json={"category_ids": [999999999]},
        headers=headers,
    )
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_admin_adds_and_deletes_company_documents(client, admin_auth, fake_upload):
    company = await _create_company()
    headers = admin_auth["headers"]

    res = await client.post(
        f"{API}/admin/companies/{company['id']}/documents",
        data={"title": "Свидетельство", "issuer": "Налоговый комитет", "issue_date": "2024-01-15"},
        files={"file": ("cert.pdf", BytesIO(b"%PDF-1.4 cert"), "application/pdf")},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    certificates = res.json()["certificates"]
    assert len(certificates) == 1
    document_id = certificates[0]["id"]
    assert fake_upload[-1].startswith(f"certificates/{company['owner_id']}/")
    stored = await fetchval("SELECT file_url FROM company_certificates WHERE id = $1", document_id)
    assert stored == fake_upload[-1]
    assert (await _last_audit(company["id"], "admin.company.document.add")) is not None

    delete_res = await client.delete(
        f"{API}/admin/companies/{company['id']}/documents/{document_id}",
        headers=headers,
    )
    assert delete_res.status_code == 200, delete_res.text
    assert delete_res.json()["certificates"] == []
    audit = await _last_audit(company["id"], "admin.company.document.delete")
    assert audit["details"]["before"]["title"] == "Свидетельство"

    missing = await client.delete(
        f"{API}/admin/companies/{company['id']}/documents/{document_id}",
        headers=headers,
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_admin_verification_status_syncs_trust_level(client, admin_auth):
    company = await _create_company()
    headers = admin_auth["headers"]

    res = await client.post(
        f"{API}/admin/companies/{company['id']}/verification-status",
        json={"status": "verified"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["verification_status"] == "verified"
    trust = await fetchval("SELECT trust_level::text FROM actors WHERE id = $1", company["actor_id"])
    assert trust == "verified"

    no_reason = await client.post(
        f"{API}/admin/companies/{company['id']}/verification-status",
        json={"status": "pending"},
        headers=headers,
    )
    assert no_reason.status_code == 422

    revoke = await client.post(
        f"{API}/admin/companies/{company['id']}/verification-status",
        json={"status": "needs_documents", "reason": "Истёк срок свидетельства"},
        headers=headers,
    )
    assert revoke.status_code == 200, revoke.text
    assert revoke.json()["verification_status"] == "needs_documents"
    trust = await fetchval("SELECT trust_level::text FROM actors WHERE id = $1", company["actor_id"])
    assert trust == "standard"
    audit = await _last_audit(company["id"], "admin.company.verification.set")
    assert audit["details"]["revoked"] is True
    assert audit["details"]["changes"]["verification_status"] == {
        "before": "verified",
        "after": "needs_documents",
    }

    approve = await client.post(
        f"{API}/admin/companies/{company['id']}/action",
        json={"action": "approve"},
        headers=headers,
    )
    assert approve.status_code == 200, approve.text
    trust = await fetchval("SELECT trust_level::text FROM actors WHERE id = $1", company["actor_id"])
    assert trust == "verified"


@pytest.mark.asyncio
async def test_change_request_actions_are_logged(client, admin_auth):
    company = await _create_company(verification="verified")
    request_id = await fetchval(
        """
        INSERT INTO company_change_requests (
            company_id, requested_by_user_id, status, proposed, current_snapshot
        )
        VALUES ($1, $2, 'pending', $3::jsonb, $4::jsonb)
        RETURNING id
        """,
        company["id"],
        company["owner_id"],
        json.dumps({"city": "Куляб"}),
        json.dumps({"city": "Душанбе"}),
    )
    res = await client.post(
        f"{API}/admin/companies/change-requests/{request_id}/action",
        json={"action": "approve"},
        headers=admin_auth["headers"],
    )
    assert res.status_code == 200, res.text
    audit = await _last_audit(company["id"], "admin.company.change_request.")
    assert audit["action"] == "admin.company.change_request.approve"
    assert audit["details"]["changes"]["city"] == {"before": "Душанбе", "after": "Куляб"}

    request_id = await fetchval(
        """
        INSERT INTO company_change_requests (
            company_id, requested_by_user_id, status, proposed, current_snapshot
        )
        VALUES ($1, $2, 'pending', $3::jsonb, $4::jsonb)
        RETURNING id
        """,
        company["id"],
        company["owner_id"],
        json.dumps({"tax_number": "000"}),
        json.dumps({"tax_number": "123456789"}),
    )
    res = await client.post(
        f"{API}/admin/companies/change-requests/{request_id}/action",
        json={"action": "reject", "reason": "Не совпадает с документами"},
        headers=admin_auth["headers"],
    )
    assert res.status_code == 200, res.text
    audit = await _last_audit(company["id"], "admin.company.change_request.")
    assert audit["action"] == "admin.company.change_request.reject"
    assert audit["details"]["reason"] == "Не совпадает с документами"


@pytest.mark.asyncio
async def test_staff_without_update_permission_cannot_manage_company(client, admin_auth, fake_upload):
    company = await _create_company()
    role_id = await create_role_with_permissions(
        client,
        admin_auth["headers"],
        f"companies-viewer-{unique_suffix()}",
        ["admin_companies.view"],
    )
    staff = await create_staff(client, admin_auth["headers"], role_id)
    headers = await login_headers(client, staff["email"])
    base = f"{API}/admin/companies/{company['id']}"

    view = await client.get(base, headers=headers)
    assert view.status_code == 200, view.text

    attempts = [
        client.patch(base, json={"city": "X"}, headers=headers),
        client.post(
            f"{base}/logo",
            files={"file": ("logo.png", BytesIO(b"png"), "image/png")},
            headers=headers,
        ),
        client.post(f"{base}/members", json={"email": "x@example.com", "role": "admin"}, headers=headers),
        client.patch(f"{base}/members/{company['owner_id']}", json={"role": "admin"}, headers=headers),
        client.delete(f"{base}/members/{company['owner_id']}", headers=headers),
        client.post(f"{base}/owner", json={"user_id": company["owner_id"]}, headers=headers),
        client.put(f"{base}/categories", json={"category_ids": []}, headers=headers),
        client.post(
            f"{base}/documents",
            data={"title": "t", "issuer": "i", "issue_date": "2024"},
            files={"file": ("c.pdf", BytesIO(b"pdf"), "application/pdf")},
            headers=headers,
        ),
        client.delete(f"{base}/documents/1", headers=headers),
        client.post(f"{base}/verification-status", json={"status": "verified"}, headers=headers),
    ]
    for attempt in attempts:
        res = await attempt
        assert res.status_code == 403, (res.request.method, res.request.url, res.text)
    assert fake_upload == []
    assert await fetchval(
        "SELECT verification_status::text FROM companies WHERE id = $1", company["id"]
    ) == "pending"
