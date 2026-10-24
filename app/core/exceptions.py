from fastapi import HTTPException, status


class AppError(HTTPException):
    def __init__(self, status_code: int, detail: str, code: str | None = None):
        super().__init__(status_code=status_code, detail=detail)
        self.code = code


class NotFoundError(AppError):
    def __init__(self, detail: str = "Resource not found"):
        super().__init__(status.HTTP_404_NOT_FOUND, detail, "not_found")


class ForbiddenError(AppError):
    def __init__(self, detail: str = "Forbidden", code: str | None = "forbidden"):
        super().__init__(status.HTTP_403_FORBIDDEN, detail, code)


class ConflictError(AppError):
    def __init__(self, detail: str = "Conflict"):
        super().__init__(status.HTTP_409_CONFLICT, detail, "conflict")


class UnauthorizedError(AppError):
    def __init__(self, detail: str = "Unauthorized"):
        super().__init__(status.HTTP_401_UNAUTHORIZED, detail, "unauthorized")


class ValidationError(AppError):
    def __init__(self, detail: str = "Validation error"):
        super().__init__(status.HTTP_422_UNPROCESSABLE_ENTITY, detail, "validation_error")


class RateLimitError(AppError):
    def __init__(self, detail: str = "Too many requests. Try again later."):
        super().__init__(status.HTTP_429_TOO_MANY_REQUESTS, detail, "rate_limited")


__all__ = [
    "AppError",
    "ConflictError",
    "ForbiddenError",
    "NotFoundError",
    "RateLimitError",
    "UnauthorizedError",
    "ValidationError",
]
