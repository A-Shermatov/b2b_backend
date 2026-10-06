import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.modules.payments.api.common import PaymentService, public_router
from app.modules.payments.services.gateway import InvalidCallbackSignature

logger = logging.getLogger(__name__)


@public_router.post("/alif/callback", include_in_schema=False)
async def alif_callback(request: Request):
    if not get_settings().alif_enabled:
        return JSONResponse({"detail": "Alif payments are disabled"}, status_code=404)
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"detail": "Invalid payload"}, status_code=400)

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
