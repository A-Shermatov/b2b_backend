from fastapi import status

from app.core.exceptions import AppError


class DcGatewayError(AppError):
    def __init__(self, detail: str = "Оплата через DC пока не поддерживается"):
        super().__init__(status.HTTP_501_NOT_IMPLEMENTED, detail, "payment_gateway_unsupported")
