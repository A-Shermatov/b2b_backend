import hashlib
import hmac
import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx
from fastapi import status

from app.config import Settings, get_settings
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)

TWO_PLACES = Decimal("0.01")


class AlifGatewayError(AppError):
    def __init__(self, detail: str = "Payment gateway error"):
        super().__init__(status.HTTP_502_BAD_GATEWAY, detail, "payment_gateway_error")


def format_amount(amount: Decimal | float | str) -> str:
    return str(Decimal(str(amount)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP))


def hmac_sha256_hex(key: str, message: str) -> str:
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


class AlifClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.alif_terminal_key or not self.settings.alif_terminal_password:
            raise AlifGatewayError("Alif terminal credentials are not configured")
        self.key = self.settings.alif_terminal_key
        self._password = self.settings.alif_terminal_password
        self.base_url = self.settings.alif_base_url.rstrip("/")

    def _secret(self) -> str:
        return hmac_sha256_hex(self.key, self._password)

    def sign(self, data: str) -> str:
        return hmac_sha256_hex(self._secret(), data)

    def _matches(self, expected_data: str, token: Any) -> bool:
        if not isinstance(token, str) or len(token) != 64:
            return False
        return hmac.compare_digest(self.sign(expected_data), token.lower())

    def verify_callback(self, payload: dict[str, Any]) -> bool:
        event = payload.get("event") or "payment"
        token = payload.get("token")
        if event == "payment":
            data = (
                f"{payload.get('orderId', '')}"
                f"{payload.get('status', '')}"
                f"{payload.get('transactionId', '')}"
            )
            return self._matches(data, token)
        if event in ("cancel", "confirm"):
            data = (
                f"{payload.get('terminal_id', '')}"
                f"{payload.get('transaction_id', '')}"
                f"{payload.get('status', '')}"
            )
            return self._matches(data, token)
        return False

    async def _post(self, path: str, body: dict[str, Any], headers: dict[str, str] | None = None):
        try:
            async with httpx.AsyncClient(timeout=self.settings.alif_timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}{path}",
                    json=body,
                    headers={"Content-Type": "application/json", **(headers or {})},
                )
        except httpx.HTTPError as exc:
            logger.warning("Alif request %s failed: %s", path, exc.__class__.__name__)
            raise AlifGatewayError("Платёжный шлюз недоступен, попробуйте позже") from exc
        try:
            data = response.json()
        except ValueError as exc:
            logger.warning("Alif %s returned non-JSON response (HTTP %s)", path, response.status_code)
            raise AlifGatewayError("Некорректный ответ платёжного шлюза") from exc
        return response.status_code, data

    async def create_payment(
        self,
        *,
        order_id: str,
        amount: Decimal,
        callback_url: str,
        return_url: str,
        info: str | None = None,
        email: str | None = None,
        phone: str | None = None,
    ) -> str:
        amount_str = format_amount(amount)
        gate = self.settings.alif_gate
        body: dict[str, Any] = {
            "key": self.key,
            "order_id": order_id,
            "amount": amount_str,
            "callback_url": callback_url,
            "return_url": return_url,
            "gate": gate,
            "token": self.sign(f"{self.key}{order_id}{amount_str}{callback_url}"),
        }
        if info:
            body["info"] = info[:255]
        if email:
            body["email"] = email
        if phone:
            body["phone"] = phone

        http_status, data = await self._post("/v2/", body, headers={"gate": gate})
        code = data.get("code") if isinstance(data, dict) else None
        url = data.get("url") if isinstance(data, dict) else None
        if http_status != 200 or code != 200 or not url:
            message = data.get("message") if isinstance(data, dict) else None
            logger.warning(
                "Alif payment init rejected: order=%s http=%s code=%s message=%s",
                order_id,
                http_status,
                code,
                message,
            )
            raise AlifGatewayError(f"Alif: {message or 'не удалось создать платёж'}")
        return url

    async def check_transaction(self, order_id: str) -> dict[str, Any] | None:
        body = {
            "orderId": order_id,
            "key": self.key,
            "token": self.sign(f"{self.key}{order_id}"),
        }
        http_status, data = await self._post("/checktxn", body)
        if http_status == 404:
            return None
        if http_status != 200 or not isinstance(data, dict) or "status" not in data:
            message = data.get("message") if isinstance(data, dict) else None
            logger.warning(
                "Alif checktxn failed: order=%s http=%s message=%s", order_id, http_status, message
            )
            raise AlifGatewayError(f"Alif: {message or 'не удалось проверить статус'}")
        signed = f"{data.get('orderId', '')}{data.get('status', '')}{data.get('transactionId', '')}"
        if not self._matches(signed, data.get("token")):
            logger.warning("Alif checktxn response signature mismatch: order=%s", order_id)
            raise AlifGatewayError("Подпись ответа платёжного шлюза не прошла проверку")
        return data
