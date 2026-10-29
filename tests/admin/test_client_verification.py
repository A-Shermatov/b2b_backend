import json

import pytest

from app.core.security_core import hash_password
from tests.sql import execute, fetch, fetchrow, fetchval
from tests.staff import (
    API,
    create_role_with_permissions,
    create_staff,
    login_headers,
    unique_suffix,
)

PASSWORD = "Individual123!"


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


async def _register_individual(client, side: str = "supplier") -> dict:
    suffix = unique_suffix()
    email = f"indiv-{suffix}@example.com"
    res = await client.post(
        f"{API}/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "first_name": "Физ",
            "last_name": f"Лицо{suffix}",
            "role": side,
        },
    )
    assert res.status_code == 200, res.text
    login = await client.post(
        f"{API}/auth/login", json={"email": email, "password": PASSWORD, "role": side}
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    me = await client.get(f"{API}/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    actor = next(a for a in me.json()["actors"] if a["side"] == side and a["kind"] == "individual")
    headers["X-Actor-Id"] = str(actor["id"])
    user_id = await fetchval("SELECT id FROM users WHERE email = $1", email)
    return {
        "email": email,
        "user_id": user_id,
        "actor_id": actor["id"],
        "headers": headers,
        "last_name": f"Лицо{suffix}",
    }


async def _upload_doc(client, headers: dict, doc_type: str = "passport"):
    return await client.post(
        f"{API}/me/verification/documents",
        headers=headers,
        files={"file": ("passport.pdf", b"%PDF-1.4 test", "application/pdf")},
        data={"doc_type": doc_type},
    )


async def _submitted_individual(client, fake_upload) -> dict:
    person = await _register_individual(client)
    res = await _upload_doc(client, person["headers"])
    assert res.status_code == 200, res.text
    res = await client.post(f"{API}/me/verification/submit", headers=person["headers"])
    assert res.status_code == 200, res.text
    return person


async def _staff_headers(client, admin_headers: dict, codes: list[str]) -> dict:
    role_id = await create_role_with_permissions(
        client, admin_headers, f"clients-{unique_suffix()}", codes
    )
    staff = await create_staff(client, admin_headers, role_id)
    return await login_headers(client, staff["email"])


@pytest.mark.asyncio
async def test_individual_submit_approve_and_public_status(client, admin_auth, fake_upload):
    person = await _register_individual(client)
    headers = person["headers"]
    admin_headers = admin_auth["headers"]

    state = await client.get(f"{API}/me/verification", headers=headers)
    assert state.status_code == 200, state.text
    body = state.json()
    assert body["status"] == "none"
    assert body["is_individual"] is True
    assert body["can_submit"] is False

    public = await client.get(f"{API}/public/suppliers/{person['actor_id']}")
    assert public.status_code == 200, public.text
    assert public.json()["verification_status"] == "unverified"

    no_docs = await client.post(f"{API}/me/verification/submit", headers=headers)
    assert no_docs.status_code == 422, no_docs.text

    bad_type = await client.post(
        f"{API}/me/verification/documents",
        headers=headers,
        files={"file": ("notes.txt", b"hello", "text/plain")},
        data={"doc_type": "other"},
    )
    assert bad_type.status_code == 422, bad_type.text
    assert fake_upload == []

    uploaded = await _upload_doc(client, headers)
    assert uploaded.status_code == 200, uploaded.text
    assert len(uploaded.json()["documents"]) == 1
    assert uploaded.json()["documents"][0]["doc_type"] == "passport"
    assert fake_upload[0].startswith(f"identity/{person['user_id']}/")

    submitted = await client.post(f"{API}/me/verification/submit", headers=headers)
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "pending"
    assert submitted.json()["submitted_at"] is not None

    again = await client.post(f"{API}/me/verification/submit", headers=headers)
    assert again.status_code == 409, again.text

    public = await client.get(f"{API}/public/suppliers/{person['actor_id']}")
    assert public.json()["verification_status"] == "pending"

    queue = await client.get(
        f"{API}/admin/individuals",
        params={"status": "queue", "side": "supplier", "query": person["email"]},
        headers=admin_headers,
    )
    assert queue.status_code == 200, queue.text
    items = queue.json()["items"]
    assert [item["id"] for item in items] == [person["user_id"]]
    assert items[0]["documents_count"] == 1
    assert items[0]["sides"] == ["supplier"]
    assert queue.json()["status_counts"]["queue"] == 1

    buyer_side = await client.get(
        f"{API}/admin/individuals",
        params={"status": "queue", "side": "buyer", "query": person["email"]},
        headers=admin_headers,
    )
    assert buyer_side.json()["items"] == []

    counts = await client.get(f"{API}/admin/verification/counts", headers=admin_headers)
    assert counts.status_code == 200, counts.text
    assert counts.json()["individuals"] >= 1

    nav = await client.get(f"{API}/admin/nav-counts", headers=admin_headers)
    assert nav.status_code == 200, nav.text
    assert nav.json()["companies"] >= 1

    detail = await client.get(
        f"{API}/admin/individuals/{person['user_id']}", headers=admin_headers
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["verification_status"] == "pending"
    assert len(detail.json()["documents"]) == 1

    no_reason = await client.post(
        f"{API}/admin/individuals/{person['user_id']}/verification",
        json={"action": "reject"},
        headers=admin_headers,
    )
    assert no_reason.status_code == 422, no_reason.text

    approved = await client.post(
        f"{API}/admin/individuals/{person['user_id']}/verification",
        json={"action": "approve"},
        headers=admin_headers,
    )
    assert approved.status_code == 200, approved.text
    approved_body = approved.json()
    assert approved_body["verification_status"] == "verified"
    assert approved_body["verified_at"] is not None
    assert approved_body["verified_by"]["email"] == "admin@example.com"
    assert approved_body["documents"][0]["status"] == "accepted"

    trust = await fetchval("SELECT trust_level::text FROM actors WHERE id = $1", person["actor_id"])
    assert trust == "verified"

    public = await client.get(f"{API}/public/suppliers/{person['actor_id']}")
    assert public.json()["verification_status"] == "verified"
    assert public.json()["trust_level"] == "verified"

    listed = await client.get(
        f"{API}/public/suppliers",
        params={"q": person["last_name"], "verified": "true"},
    )
    assert listed.status_code == 200, listed.text
    assert [item["actor_id"] for item in listed.json()["items"]] == [person["actor_id"]]

    audit = await fetchrow(
        """
        SELECT details FROM audit_logs
        WHERE action = 'admin.individual.verification.set'
          AND resource_type = 'user' AND resource_id = $1
        ORDER BY id DESC LIMIT 1
        """,
        str(person["user_id"]),
    )
    details = audit["details"]
    if isinstance(details, str):
        details = json.loads(details)
    assert details["changes"]["individual_verification_status"] == {
        "before": "pending",
        "after": "verified",
    }

    notifications = await fetch(
        "SELECT title FROM notifications WHERE user_id = $1", person["user_id"]
    )
    assert any(row["title"] == "Личность подтверждена" for row in notifications)

    locked = await _upload_doc(client, headers)
    assert locked.status_code == 409, locked.text

    revoked = await client.post(
        f"{API}/admin/individuals/{person['user_id']}/verification",
        json={"action": "set", "status": "rejected", "reason": "Документ поддельный"},
        headers=admin_headers,
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["verification_status"] == "rejected"
    assert revoked.json()["verification_reason"] == "Документ поддельный"
    trust = await fetchval("SELECT trust_level::text FROM actors WHERE id = $1", person["actor_id"])
    assert trust == "basic"
    public = await client.get(f"{API}/public/suppliers/{person['actor_id']}")
    assert public.json()["verification_status"] == "rejected"

    mine = await client.get(f"{API}/me/verification", headers=headers)
    assert mine.json()["status"] == "rejected"
    assert mine.json()["reason"] == "Документ поддельный"
    assert mine.json()["can_submit"] is True


@pytest.mark.asyncio
async def test_request_documents_and_admin_documents(client, admin_auth, fake_upload):
    person = await _submitted_individual(client, fake_upload)
    admin_headers = admin_auth["headers"]

    requested = await client.post(
        f"{API}/admin/individuals/{person['user_id']}/verification",
        json={"action": "request_documents", "reason": "Нужно селфи с паспортом"},
        headers=admin_headers,
    )
    assert requested.status_code == 200, requested.text
    assert requested.json()["verification_status"] == "needs_documents"

    added = await client.post(
        f"{API}/admin/individuals/{person['user_id']}/documents",
        headers=admin_headers,
        files={"file": ("selfie.jpg", b"jpeg", "image/jpeg")},
        data={"doc_type": "selfie"},
    )
    assert added.status_code == 200, added.text
    docs = added.json()["documents"]
    assert len(docs) == 2
    admin_doc = next(doc for doc in docs if doc["doc_type"] == "selfie")
    assert admin_doc["uploaded_by_admin"] is True

    removed = await client.delete(
        f"{API}/admin/individuals/{person['user_id']}/documents/{admin_doc['id']}",
        headers=admin_headers,
    )
    assert removed.status_code == 200, removed.text
    assert len(removed.json()["documents"]) == 1

    mine = await client.get(f"{API}/me/verification", headers=person["headers"])
    own_doc = mine.json()["documents"][0]
    deleted = await client.delete(
        f"{API}/me/verification/documents/{own_doc['id']}", headers=person["headers"]
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["documents"] == []

    actions = {
        row["action"]
        for row in await fetch(
            "SELECT action FROM audit_logs WHERE resource_type = 'user' AND resource_id = $1",
            str(person["user_id"]),
        )
    }
    assert {
        "verification.individual.submit",
        "admin.individual.document.add",
        "admin.individual.document.delete",
        "verification.individual.document.delete",
    } <= actions


@pytest.mark.asyncio
async def test_individual_permissions(client, admin_auth, fake_upload):
    person = await _submitted_individual(client, fake_upload)
    admin_headers = admin_auth["headers"]

    no_perms = await _staff_headers(client, admin_headers, ["admin_dashboard.view"])
    res = await client.get(f"{API}/admin/individuals", headers=no_perms)
    assert res.status_code == 403, res.text
    res = await client.post(
        f"{API}/admin/individuals/{person['user_id']}/verification",
        json={"action": "approve"},
        headers=no_perms,
    )
    assert res.status_code == 403, res.text
    res = await client.get(f"{API}/admin/users/{person['user_id']}/dossier", headers=no_perms)
    assert res.status_code == 403, res.text

    viewer = await _staff_headers(client, admin_headers, ["admin_users.view"])
    res = await client.get(f"{API}/admin/individuals", headers=viewer)
    assert res.status_code == 200, res.text
    res = await client.post(
        f"{API}/admin/individuals/{person['user_id']}/verification",
        json={"action": "approve"},
        headers=viewer,
    )
    assert res.status_code == 403, res.text

    editor = await _staff_headers(
        client, admin_headers, ["admin_users.view", "admin_users.update"]
    )
    res = await client.post(
        f"{API}/admin/individuals/{person['user_id']}/verification",
        json={"action": "approve"},
        headers=editor,
    )
    assert res.status_code == 200, res.text
    assert res.json()["verification_status"] == "verified"

    staff_self = await client.get(f"{API}/me/verification", headers=editor)
    assert staff_self.status_code == 403, staff_self.text


async def _create_user(email: str, role: str = "buyer") -> int:
    return await fetchval(
        """
        INSERT INTO users (
            email, password_hash, first_name, last_name, role, role_id, status,
            created_at, updated_at
        )
        VALUES (
            $1, $2, 'Dossier', 'Owner', $3::user_role,
            (SELECT id FROM roles WHERE name = $4),
            'active'::user_status, NOW(), NOW()
        )
        RETURNING id
        """,
        email,
        hash_password(PASSWORD),
        role,
        role,
    )


async def _create_company(owner_id: int, title: str, side: str, verification: str) -> dict:
    company_id = await fetchval(
        """
        INSERT INTO companies (
            title, actor_type, owner_id, legal_name, tax_number, city, country,
            verification_status, operational_status, rating, created_at, updated_at
        )
        VALUES (
            $1, $2::actor_type, $3, 'ООО Досье', '987654321', 'Душанбе', 'Таджикистан',
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
        VALUES ('company', $1::actor_side, NULL, $2, $3, 'standard', TRUE, NOW(), NOW())
        RETURNING id
        """,
        side,
        company_id,
        title,
    )
    return {"id": company_id, "actor_id": actor_id, "title": title}


@pytest.mark.asyncio
async def test_user_dossier(client, admin_auth):
    suffix = unique_suffix()
    owner_id = await _create_user(f"dossier-{suffix}@example.com", role="supplier")
    owned = await _create_company(owner_id, f"Dossier Own {suffix}", "supplier", "verified")
    other_owner = await _create_user(f"dossier-other-{suffix}@example.com", role="buyer")
    member_of = await _create_company(other_owner, f"Dossier Member {suffix}", "buyer", "pending")
    await execute(
        "INSERT INTO company_users (company_id, user_id, role) VALUES ($1, $2, 'accountant')",
        member_of["id"],
        owner_id,
    )
    for _ in range(6):
        await execute(
            """
            INSERT INTO audit_logs (user_id, action, resource_type, resource_id, created_at)
            VALUES (NULL, 'login_failed', 'user', $1, NOW())
            """,
            str(owner_id),
        )
    await execute(
        """
        INSERT INTO audit_logs (user_id, action, resource_type, resource_id, details, created_at)
        VALUES (NULL, 'admin.user.status.blocked', 'user', $1, $2::jsonb, NOW())
        """,
        str(owner_id),
        json.dumps({"reason": "Спам"}),
    )

    res = await client.get(
        f"{API}/admin/users/{owner_id}/dossier", headers=admin_auth["headers"]
    )
    assert res.status_code == 200, res.text
    body = res.json()
    companies = {company["id"]: company for company in body["companies"]}
    assert set(companies) == {owned["id"], member_of["id"]}
    assert companies[owned["id"]]["is_owner"] is True
    assert companies[owned["id"]]["role"] == "director"
    assert companies[owned["id"]]["sides"] == ["supplier"]
    assert companies[member_of["id"]]["role"] == "accountant"
    assert companies[member_of["id"]]["verification_status"] == "pending"

    signals = body["signals"]
    assert signals["failed_logins_30d"] == 6
    assert signals["blocks_count"] == 1
    assert signals["contracts_total"] == 0
    assert signals["disputes_total"] == 0
    assert body["blocks"][0]["reason"] == "Спам"
    assert body["identity"]["status"] == "none"
    assert body["money"]["pending_withdrawals_count"] == 0
    assert body["user"]["account_age_days"] == 0
    assert body["risk"]["level"] in {"medium", "high"}
    codes = {reason["code"] for reason in body["risk"]["reasons"]}
    assert {"block_history", "failed_logins", "new_account"} <= codes

    missing = await client.get(f"{API}/admin/users/999999999/dossier", headers=admin_auth["headers"])
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_company_verification_queue_filters_actor_type(client, admin_auth):
    suffix = unique_suffix()
    owner_id = await _create_user(f"queue-{suffix}@example.com", role="both")
    supplier = await _create_company(owner_id, f"Queue {suffix} S", "supplier", "pending")
    buyer = await _create_company(owner_id, f"Queue {suffix} B", "buyer", "needs_documents")
    headers = admin_auth["headers"]

    async def queue(actor_type: str) -> list[int]:
        res = await client.get(
            f"{API}/admin/companies",
            params={"status": "pending", "actor_type": actor_type, "query": f"Queue {suffix}"},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        return [item["id"] for item in res.json()["items"]]

    assert await queue("supplier") == [supplier["id"]]
    assert await queue("buyer") == [buyer["id"]]

    counts = await client.get(f"{API}/admin/verification/counts", headers=headers)
    assert counts.status_code == 200, counts.text
    body = counts.json()
    assert body["supplier_companies"] >= 1
    assert body["buyer_companies"] >= 1
