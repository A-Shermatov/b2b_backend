import json
from datetime import datetime

from app.core.middleware._common import redact_data
from app.modules.companies.schemas import CompanyUserSchema, CompanyWithRelations
from app.modules.companies.services.common import to_public_company_detail
from app.modules.payments.gateways.http_log import sanitize_for_log, sanitize_raw_for_log


def test_audit_redacts_sensitive_keys():
    data = {
        "code": "123456",
        "otp": "1",
        "totp": "2",
        "backup_code": "3",
        "challenge_token": "4",
        "token": "5",
        "access_token": "6",
        "refresh_token": "7",
        "Authorization": "Bearer x",
        "details": {"card": "4111"},
        "card": "4111",
        "account_number": "1",
        "iban": "2",
        "pan": "3",
        "cvv": "4",
        "secret": "5",
        "key": "6",
        "password": "7",
        "new_password": "8",
        "current_password": "9",
    }
    assert set(redact_data(data).values()) == {"***"}


def test_audit_key_match_is_exact():
    data = {"monkey": "a", "keyboard": "b", "amount": 10, "nested": [{"KEY": "x", "title": "t"}]}
    out = redact_data(data)
    assert out["monkey"] == "a"
    assert out["keyboard"] == "b"
    assert out["amount"] == 10
    assert out["nested"] == [{"KEY": "***", "title": "t"}]


def test_audit_redacts_credential_like_fields():
    data = {"bot_token": "123:abc", "api_key": "k", "client_secret": "s", "smtp_password": "p", "channel_id": "@c"}
    out = redact_data(data)
    assert out["channel_id"] == "@c"
    assert {out[k] for k in ("bot_token", "api_key", "client_secret", "smtp_password")} == {"***"}


def test_gateway_sanitize_headers_and_body():
    headers = {
        "Authorization": "Bearer abc",
        "Cookie": "s=1",
        "Set-Cookie": "s=2",
        "X-Api-Key": "k",
        "Content-Type": "application/json",
    }
    out = sanitize_for_log(headers)
    assert out["Content-Type"] == "application/json"
    assert all(out[k] == "***" for k in ("Authorization", "Cookie", "Set-Cookie", "X-Api-Key"))

    body = {
        "order_id": "A1",
        "amount": 100,
        "status": "ok",
        "key": "k",
        "token": "t",
        "cvv": "123",
        "items": [{"pan": "4111", "Card": "x", "name": "n"}],
        "meta": {"Password": "p", "secret": "s"},
    }
    out = sanitize_for_log(body)
    assert out["order_id"] == "A1"
    assert out["amount"] == 100
    assert out["status"] == "ok"
    assert out["key"] == out["token"] == out["cvv"] == "***"
    assert out["items"] == [{"pan": "***", "Card": "***", "name": "n"}]
    assert out["meta"] == {"Password": "***", "secret": "***"}


def test_gateway_raw_json_is_sanitized_and_text_left_alone():
    raw = json.dumps({"order_id": "A1", "token": "t", "nested": {"key": "k"}})
    out = json.loads(sanitize_raw_for_log(raw))
    assert out == {"order_id": "A1", "token": "***", "nested": {"key": "***"}}
    assert sanitize_raw_for_log("<xml>token=1</xml>") == "<xml>token=1</xml>"
    assert sanitize_raw_for_log("123") == "123"
    assert sanitize_raw_for_log(None) is None


def test_public_company_detail_excludes_private_fields():
    now = datetime(2026, 1, 1)
    company = CompanyWithRelations(
        id=1,
        title="Acme",
        actor_type="supplier",
        actor_types=["supplier", "buyer"],
        owner_id=42,
        team_members=[42, 43],
        legal_name="Acme LLC",
        tax_number="123456789",
        website=None,
        description=None,
        logo=None,
        country="TJ",
        city="Dushanbe",
        address="Street 1",
        verification_status="verified",
        rating=4.5,
        created_at=now,
        updated_at=now,
        company_users=[
            CompanyUserSchema(id=1, company_id=1, user_id=42, role="director", email="a@b.c")
        ],
    )
    out = to_public_company_detail(company).model_dump(mode="json")
    for field in ("owner_id", "team_members", "tax_number", "address", "company_users", "reviews"):
        assert field not in out
    assert out["has_tax_number"] is True
    assert out["city"] == "Dushanbe"
    assert "a@b.c" not in json.dumps(out)
