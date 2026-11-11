"""Request dates with a time of day, the work deadline of a service, a free title."""

import pytest

from app.modules.rfqs.schemas.common import clean_request_date, clean_title
from tests.buyer.test_deadlines_selfdeal import DEADLINE_MESSAGE, _publish_rfq, _rfq_payload, _submit
from tests.sql import execute, fetchval

API = "/api/v1"


def test_request_date_is_a_day_or_a_day_with_minutes():
    assert clean_request_date("2026-11-20") == "2026-11-20"
    assert clean_request_date("2026-11-20T18:30") == "2026-11-20T18:30"
    assert clean_request_date("2026-11-20 18:30:00") == "2026-11-20T18:30"
    assert clean_request_date(None) is None
    for bad in ("", "2026-13-01", "2026-11-20T25:00", "tomorrow", "2026-11-20T18:30+05:00"):
        with pytest.raises(ValueError):
            clean_request_date(bad)


def test_title_has_no_minimum_length():
    assert clean_title("  A  ") == "A"
    assert clean_title("x" * 500) == "x" * 500
    for bad in ("", "   ", "x" * 501):
        with pytest.raises(ValueError):
            clean_title(bad)


@pytest.mark.asyncio
async def test_short_title_and_dates_with_time_are_stored(client, buyer_auth):
    payload = {
        **_rfq_payload("A", deadline="2099-12-31 18:30"),
        "start_date": "2099-12-31",
        "delivery_date": "2100-01-20T09:00",
    }
    res = await client.post(f"{API}/buyer/rfqs/", headers=buyer_auth["headers"], json=payload)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["title"] == "A"
    assert body["deadline"] == "2099-12-31T18:30"
    assert body["delivery_date"] == "2100-01-20T09:00"

    res = await client.patch(
        f"{API}/buyer/rfqs/{body['id']}",
        headers=buyer_auth["headers"],
        json={"delivery_date": "2100-02-01"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["delivery_date"] == "2100-02-01"

    res = await client.post(
        f"{API}/buyer/rfqs/",
        headers=buyer_auth["headers"],
        json={**payload, "deadline": "2099-12-31T25:00"},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_deadline_with_time_runs_out_at_that_minute(client, buyer_auth, supplier_auth):
    zone = "Asia/Dushanbe"
    stamp = "TO_CHAR((NOW() AT TIME ZONE '%s') %s INTERVAL '5 minutes', 'YYYY-MM-DD\"T\"HH24:MI')"

    rfq_id = await _publish_rfq(client, buyer_auth["headers"], "Deadline by the minute RFQ")
    await execute(f"UPDATE rfqs SET deadline = {stamp % (zone, '+')} WHERE id = $1", rfq_id)
    res = await _submit(client, supplier_auth["headers"], rfq_id)
    assert res.status_code == 200, res.text

    rfq_id = await _publish_rfq(client, buyer_auth["headers"], "Deadline just passed RFQ")
    await execute(f"UPDATE rfqs SET deadline = {stamp % (zone, '-')} WHERE id = $1", rfq_id)
    res = await _submit(client, supplier_auth["headers"], rfq_id)
    assert res.status_code == 409, res.text
    assert DEADLINE_MESSAGE in res.text


@pytest.mark.asyncio
async def test_today_without_a_time_is_still_open(client, buyer_auth, supplier_auth):
    rfq_id = await _publish_rfq(client, buyer_auth["headers"], "Deadline today RFQ")
    await execute(
        "UPDATE rfqs SET deadline = TO_CHAR(NOW() AT TIME ZONE 'Asia/Dushanbe', 'YYYY-MM-DD') WHERE id = $1",
        rfq_id,
    )
    res = await _submit(client, supplier_auth["headers"], rfq_id)
    assert res.status_code == 200, res.text
    assert await fetchval("SELECT status::text FROM rfqs WHERE id = $1", rfq_id) != "expired"
