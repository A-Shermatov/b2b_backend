import hashlib
import hmac
import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx
from fastapi import status

from app.config import Settings, get_settings
from app.core.exceptions import AppError
from app.modules.payments.gateways.http_log import (
    extract_order_id,
    log_gateway_request,
    log_gateway_response,
)

logger = logging.getLogger(__name__)

TWO_PLACES = Decimal("0.01")
# Alif Marketplace rejects any mpTerminalInfo part below 1 TJS ("Сумма должна быть от 1 смн").
MIN_MARKETPLACE_PART = Decimal("1.00")

_PATH_OPERATIONS = {
    "/v2/": "create_payment",
    "/v2": "create_payment",
    "/checktxn": "checktxn",
    "/confirm-delivery": "confirm_delivery",
    "/cancel/marketplace": "cancel_marketplace",
}


class AlifGatewayError(AppError):
    def __init__(self, detail: str = "Payment gateway error"):
        super().__init__(status.HTTP_502_BAD_GATEWAY, detail, "payment_gateway_error")


def format_amount(amount: Decimal | float | str) -> str:
    return str(Decimal(str(amount)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP))


def hmac_sha256_hex(key: str, message: str) -> str:
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def marketplace_commission(total: Decimal | float, commission: Decimal | float) -> Decimal:
    """Commission that keeps both marketplace parts at or above MIN_MARKETPLACE_PART.

    A commission below the minimum is raised to it; when the total is too small to
    split, the whole amount goes to the supplier and the commission is dropped.
    A single-part payment (no commission, or commission equal to the total) is kept.
    """
    total = Decimal(format_amount(total))
    commission = min(Decimal(format_amount(max(Decimal("0"), Decimal(str(commission))))), total)
    if commission <= 0 or commission == total:
        return commission
    if total < 2 * MIN_MARKETPLACE_PART:
        return Decimal("0.00")
    commission = max(commission, MIN_MARKETPLACE_PART)
    return min(commission, total - MIN_MARKETPLACE_PART)


def build_mp_terminal_info(
    *,
    total: Decimal,
    commission: Decimal,
    supplier_terminal_id: str,
    platform_terminal_id: str,
    title: str,
) -> list[dict[str, Any]]:
    """Split total into supplier + platform commission for Alif Marketplace mpTerminalInfo."""
    total = Decimal(format_amount(total))
    commission = marketplace_commission(total, commission)
    supplier_amount = total - commission
    entries: list[dict[str, Any]] = []
    if supplier_amount > 0:
        entries.append(
            {
                "terminal_id": str(supplier_terminal_id),
                "amount": float(supplier_amount),
                "condition_id": 0,
                "invoices": [
                    {
                        "name": (title or "Услуга")[:120],
                        "category": "services",
                        "quantity": 1,
                        "price": float(supplier_amount),
                    }
                ],
            }
        )
    if commission > 0:
        entries.append(
            {
                "terminal_id": str(platform_terminal_id),
                "amount": float(commission),
                "condition_id": 0,
                "invoices": [
                    {
                        "name": "Комиссия площадки",
                        "category": "services",
                        "quantity": 1,
                        "price": float(commission),
                    }
                ],
            }
        )
    if not entries:
        entries.append(
            {
                "terminal_id": str(supplier_terminal_id),
                "amount": float(total),
                "condition_id": 0,
                "invoices": [
                    {
                        "name": (title or "Услуга")[:120],
                        "category": "services",
                        "quantity": 1,
                        "price": float(total),
                    }
                ],
            }
        )
    return entries


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

    async def _post(
        self,
        path: str,
        body: dict[str, Any],
        headers: dict[str, str] | None = None,
        *,
        operation: str | None = None,
        order_id: str | None = None,
    ):
        url = f"{self.base_url}{path}"
        op = operation or _PATH_OPERATIONS.get(path, path.strip("/") or "request")
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        log_id = await log_gateway_request(
            gateway="alif",
            direction="outbound",
            operation=op,
            method="POST",
            url=url,
            request_headers=request_headers,
            request_body=body,
            order_id=order_id or extract_order_id(body),
        )
        try:
            async with httpx.AsyncClient(timeout=self.settings.alif_timeout_seconds) as client:
                response = await client.post(url, json=body, headers=request_headers)
        except httpx.HTTPError as exc:
            await log_gateway_response(
                log_id,
                error=f"{exc.__class__.__name__}: {exc}",
            )
            logger.warning("Alif request %s failed: %s", path, exc.__class__.__name__)
            raise AlifGatewayError("Платёжный шлюз недоступен, попробуйте позже") from exc

        raw_text = response.text
        try:
            data = response.json()
        except ValueError as exc:
            # Persist raw bank body before raising / processing.
            await log_gateway_response(
                log_id,
                response_status=response.status_code,
                response_raw=raw_text,
                error="non_json_response",
            )
            logger.warning("Alif %s returned non-JSON response (HTTP %s)", path, response.status_code)
            raise AlifGatewayError("Некорректный ответ платёжного шлюза") from exc

        await log_gateway_response(
            log_id,
            response_status=response.status_code,
            response_body=data,
            response_raw=raw_text,
            order_id=extract_order_id(data) if isinstance(data, dict) else None,
        )
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
        mp_terminal_info: list[dict[str, Any]] | None = None,
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

        headers: dict[str, str] = {"gate": gate}
        if mp_terminal_info:
            body["mpTerminalInfo"] = mp_terminal_info
            headers["isMarketPlace"] = "true"

        http_status, data = await self._post("/v2/", body, headers=headers)
        code = data.get("code") if isinstance(data, dict) else None
        url = data.get("url") if isinstance(data, dict) else None
        if http_status != 200 or code != 200 or not url:
            message = data.get("message") if isinstance(data, dict) else None
            logger.warning(
                "Alif payment init rejected: order=%s http=%s code=%s message=%s marketplace=%s",
                order_id,
                http_status,
                code,
                message,
                bool(mp_terminal_info),
            )
            if http_status == 401 or code == 401:
                raise AlifGatewayError(
                    "Alif отклонил доступ (401). Проверьте ALIF_TERMINAL_KEY, "
                    "ALIF_TERMINAL_PASSWORD и что терминал активен для "
                    f"{self.base_url} / gate={gate}."
                )
            raise AlifGatewayError(f"Alif: {message or 'не удалось создать платёж'}")
        return url

    async def confirm_delivery(
        self, *, transaction_id: str, amount: Decimal, order_id: str | None = None
    ) -> dict[str, Any]:
        amount_str = format_amount(amount)
        body = {
            "key": self.key,
            "transaction_id": str(transaction_id),
            "amount": float(Decimal(amount_str)),
            "token": self.sign(f"{self.key}{transaction_id}{amount_str}"),
        }
        http_status, data = await self._post("/confirm-delivery", body, order_id=order_id)
        if http_status != 200 or (isinstance(data, dict) and data.get("code") not in (None, 200, "200")):
            message = data.get("message") if isinstance(data, dict) else None
            logger.warning(
                "Alif confirm-delivery failed: txn=%s http=%s data=%s",
                transaction_id,
                http_status,
                data,
            )
            raise AlifGatewayError(f"Alif confirm-delivery: {message or 'ошибка подтверждения'}")
        return data if isinstance(data, dict) else {"raw": data}

    async def cancel_marketplace(
        self,
        *,
        transaction_id: str,
        amount: Decimal,
        reason: str = "Отмена сделки",
        order_id: str | None = None,
    ) -> dict[str, Any]:
        amount_str = format_amount(amount)
        body = {
            "key": self.key,
            "transaction_id": str(transaction_id),
            "amount": float(Decimal(amount_str)),
            "reason": reason[:255],
            "token": self.sign(f"{self.key}{transaction_id}{amount_str}"),
        }
        http_status, data = await self._post("/cancel/marketplace", body, order_id=order_id)
        if http_status != 200 or (isinstance(data, dict) and data.get("code") not in (None, 200, "200")):
            message = data.get("message") if isinstance(data, dict) else None
            logger.warning(
                "Alif cancel/marketplace failed: txn=%s http=%s data=%s",
                transaction_id,
                http_status,
                data,
            )
            raise AlifGatewayError(f"Alif cancel: {message or 'ошибка отмены'}")
        return data if isinstance(data, dict) else {"raw": data}

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
