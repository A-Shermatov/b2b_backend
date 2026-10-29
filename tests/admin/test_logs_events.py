import pytest

from app.modules.admin.services.logs_catalog import event_category
from tests.admin.test_contracts import _create_contract, _create_draft_rfq
from tests.staff import create_role_with_permissions, unique_suffix

API = "/api/v1"


async def _logs(client, headers, **params):
    res = await client.get(f"{API}/admin/logs", params=params, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


async def _entity(client, headers, resource_type, resource_id):
    res = await client.get(
        f"{API}/admin/logs/entity",
        params={"resource_type": resource_type, "resource_id": str(resource_id)},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return res.json()


@pytest.mark.parametrize(
    ("action", "category"),
    [
        ("login", "auth"),
        ("login_failed", "auth"),
        ("password_reset", "auth"),
        ("auth.logout", "auth"),
        ("admin.company.update", "admin.company"),
        ("admin.individual.verification.set", "admin.user"),
        ("admin.role.create", "admin.staff"),
        ("platform_settings.update", "admin.staff"),
        ("rfq.create", "rfq"),
        ("proposal.accept", "proposal"),
        ("contract.submission.approve", "contract"),
        ("payment.milestone.fund", "payment"),
        ("withdrawal.request", "finance"),
        ("dispute.open", "dispute"),
        ("something.new", "other"),
    ],
)
def test_event_category(action, category):
    assert event_category(action) == category


@pytest.mark.asyncio
async def test_events_source_and_filters(client, admin_auth, buyer_auth):
    if not buyer_auth["actor_id"]:
        pytest.skip("No buyer actor")
    title = f"Events RFQ {unique_suffix()}"
    rfq_id = await _create_draft_rfq(client, buyer_auth, title)

    events = await _logs(
        client, admin_auth["headers"], source="events", audience="users", category="rfq"
    )
    created = next(
        item for item in events["items"] if item["resource_id"] == rfq_id
    )
    assert created["source"] == "event"
    assert created["action"] == "rfq.create"
    assert created["category"] == "rfq"
    assert created["resource_type"] == "rfq"
    assert created["details"]["title"] == title
    assert created["user"]["email"] == "buyer@example.com"
    assert created["actor_id"] == buyer_auth["actor_id"]
    assert all(not item["action"].startswith("http.") for item in events["items"])

    by_resource = await _logs(
        client,
        admin_auth["headers"],
        source="events",
        resource_type="rfq",
        resource_id=rfq_id,
    )
    assert [item["action"] for item in by_resource["items"]] == ["rfq.create"]

    by_action = await _logs(
        client, admin_auth["headers"], source="events", action="rfq.create", query="buyer@example"
    )
    assert by_action["items"]
    assert {item["action"] for item in by_action["items"]} == {"rfq.create"}

    by_user = await _logs(
        client,
        admin_auth["headers"],
        source="events",
        user_id=created["user"]["id"],
        category="rfq",
    )
    assert any(item["resource_id"] == rfq_id for item in by_user["items"])

    other_category = await _logs(
        client, admin_auth["headers"], source="events", category="contract", resource_id=rfq_id
    )
    assert other_category["items"] == []

    detail = await client.get(f"{API}/admin/logs/{created['id']}", headers=admin_auth["headers"])
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["source"] == "event"
    assert body["action"] == "rfq.create"
    assert "user_agent" in body

    http = await _logs(client, admin_auth["headers"], source="http", section="rfqs", kind="create")
    assert http["items"]
    assert all(item["source"] == "http" for item in http["items"])

    default = await _logs(client, admin_auth["headers"], section="rfqs", kind="create")
    assert all(item["source"] == "http" for item in default["items"])

    mixed = await _logs(client, admin_auth["headers"], source="all", query="buyer@example.com")
    assert {item["source"] for item in mixed["items"]} <= {"http", "event"}


@pytest.mark.asyncio
async def test_staff_events_audience(client, admin_auth):
    role_name = f"events-role-{unique_suffix()}"
    role_id = await create_role_with_permissions(client, admin_auth["headers"], role_name, [])

    staff = await _logs(
        client, admin_auth["headers"], source="events", audience="staff", category="admin.staff"
    )
    item = next(
        entry
        for entry in staff["items"]
        if entry["action"] == "admin.role.create" and entry["resource_id"] == str(role_id)
    )
    assert item["audience"] == "staff"
    assert item["details"]["name"] == role_name

    users = await _logs(
        client, admin_auth["headers"], source="events", audience="users", category="admin.staff"
    )
    assert all(entry["audience"] == "users" for entry in users["items"])


@pytest.mark.asyncio
async def test_contract_flow_events_and_entity_timeline(
    client, admin_auth, buyer_auth, supplier_auth
):
    if not buyer_auth["actor_id"] or not supplier_auth["actor_id"]:
        pytest.skip("No buyer/supplier actor")
    contract_id = await _create_contract(
        client, buyer_auth, supplier_auth, f"Events contract {unique_suffix()}"
    )

    contract = (
        await client.get(f"{API}/buyer/contracts/{contract_id}", headers=buyer_auth["headers"])
    ).json()
    for milestone in contract["payment_plan"]["milestones"]:
        fund = await client.post(
            f"{API}/buyer/payments/milestones/{milestone['id']}/fund",
            headers=buyer_auth["headers"],
        )
        assert fund.status_code == 200, fund.text

    submit = await client.post(
        f"{API}/supplier/contracts/{contract_id}/submissions",
        headers=supplier_auth["headers"],
        json={"type": "work", "note": "Готово", "file_names": ["report.pdf"], "assets": []},
    )
    assert submit.status_code == 200, submit.text
    submission_id = submit.json()["submissions"][0]["id"]

    approve = await client.post(
        f"{API}/buyer/contracts/{contract_id}/submissions/{submission_id}/approve",
        headers=buyer_auth["headers"],
    )
    assert approve.status_code == 200, approve.text

    timeline = await _entity(client, admin_auth["headers"], "contract", contract_id)
    actions = [item["action"] for item in timeline["items"]]
    for expected in (
        "contract.create",
        "payment.milestone.fund",
        "contract.activate",
        "contract.submission.submit",
        "contract.submission.approve",
        "contract.complete",
    ):
        assert expected in actions, actions
    complete = next(item for item in timeline["items"] if item["action"] == "contract.complete")
    assert complete["details"]["after"] == {"status": "completed"}
    assert timeline["total"] == len(timeline["items"])

    proposal_id = next(
        item["details"]["proposal_id"]
        for item in timeline["items"]
        if item["action"] == "contract.create"
    )
    proposal_timeline = await _entity(client, admin_auth["headers"], "proposal", proposal_id)
    proposal_actions = [item["action"] for item in proposal_timeline["items"]]
    assert "proposal.submit" in proposal_actions
    accepted = next(
        item for item in proposal_timeline["items"] if item["action"] == "proposal.accept"
    )
    assert accepted["details"]["contract_id"] == contract_id
    assert accepted["details"]["after"] == {"status": "accepted"}


@pytest.mark.asyncio
async def test_rfq_publish_and_close_events(client, admin_auth, buyer_auth):
    if not buyer_auth["actor_id"]:
        pytest.skip("No buyer actor")
    rfq_id = await _create_draft_rfq(client, buyer_auth, f"Close events {unique_suffix()}")
    publish = await client.post(f"{API}/buyer/rfqs/{rfq_id}/publish", headers=buyer_auth["headers"])
    assert publish.status_code == 200, publish.text
    close = await client.post(f"{API}/buyer/rfqs/{rfq_id}/close", headers=buyer_auth["headers"])
    assert close.status_code == 200, close.text

    timeline = await _entity(client, admin_auth["headers"], "rfq", rfq_id)
    assert [item["action"] for item in timeline["items"]] == [
        "rfq.close",
        "rfq.publish",
        "rfq.create",
    ]


@pytest.mark.asyncio
async def test_entity_logs_require_permission(client, buyer_auth):
    res = await client.get(
        f"{API}/admin/logs/entity",
        params={"resource_type": "rfq", "resource_id": "1"},
        headers=buyer_auth["headers"],
    )
    assert res.status_code in (401, 403)
