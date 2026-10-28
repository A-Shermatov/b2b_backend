import json
import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.modules.payments.api.common import PaymentService, public_router
from app.modules.payments.gateways.http_log import extract_order_id, log_gateway_inbound
from app.modules.payments.services.gateway import InvalidCallbackSignature

logger = logging.getLogger(__name__)


@public_router.post("/alif/callback", include_in_schema=False)
async def alif_callback(request: Request):
    if not get_settings().alif_enabled:
        return JSONResponse({"detail": "Alif payments are disabled"}, status_code=404)

    raw_body = await request.body()
    raw_text = raw_body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw_text) if raw_text else None
    except json.JSONDecodeError:
        await log_gateway_inbound(
            gateway="alif",
            operation="callback",
            request_body={"raw": raw_text[:20000]},
            url=str(request.url),
            request_headers=dict(request.headers),
        )
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
    if not isinstance(payload, dict):
        await log_gateway_inbound(
            gateway="alif",
            operation="callback",
            request_body=payload if payload is not None else {"raw": raw_text[:20000]},
            url=str(request.url),
            request_headers=dict(request.headers),
        )
        return JSONResponse({"detail": "Invalid payload"}, status_code=400)

    # Persist bank callback as received, before signature check / status apply.
    await log_gateway_inbound(
        gateway="alif",
        operation="callback",
        request_body=payload,
        url=str(request.url),
        request_headers=dict(request.headers),
        order_id=extract_order_id(payload),
    )

    try:
        result = await PaymentService().handle_alif_callback(payload)
    except InvalidCallbackSignature:
        logger.warning(
            "Alif callback rejected: invalid signature (order=%s, transaction=%s)",
            payload.get("orderId"),
            payload.get("transactionId") or payload.get("transaction_id"),
        )
        return JSONResponse({"detail": "Invalid signature"}, status_code=400)
    return JSONResponse({"ok": True, **result}, status_code=200)
