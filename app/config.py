import re
from functools import lru_cache
from typing import List, Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_SECRET_KEY = "change-me-to-a-long-random-secret-key-in-production"
DEV_ENVS = frozenset({"development", "dev", "local", "test", "testing"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "BrandMarket API"
    # Safe by default: a server started without APP_ENV must not run with mock
    # payments, open /docs and the public default SECRET_KEY. Local work sets
    # APP_ENV=development in .env (see .env.example).
    app_env: str = "production"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    # Request deadlines are wall-clock time of the market the platform works in:
    # "до 18:00" means 18:00 there, whatever zone the server or database runs in.
    business_timezone: str = "Asia/Dushanbe"

    secret_key: str = DEFAULT_SECRET_KEY
    totp_encryption_key: str | None = None
    totp_issuer: str = "Brand Market"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7
    two_fa_challenge_expire_minutes: int = 5
    bcrypt_rounds: int = 12
    algorithm: str = "HS256"

    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    trust_proxy_headers: bool = True
    # CF-Connecting-IP is set by Cloudflare and cannot be forged through it. Behind
    # any other proxy (nginx, a load balancer) a client can send it: turn this off.
    trust_cf_connecting_ip: bool = True

    database_url: str
    redis_url: str | None = None
    rate_limit_login_per_minute: int = 5
    rate_limit_register_per_minute: int = 5
    rate_limit_refresh_per_minute: int = 30
    rate_limit_2fa_verify_per_minute: int = 10
    rate_limit_2fa_setup_per_minute: int = 5
    rate_limit_global_per_minute: int = 300
    # Per-account limits (independent of the client IP).
    rate_limit_login_per_account: int = 10
    rate_limit_login_account_window_seconds: int = 900
    rate_limit_2fa_per_user: int = 5
    rate_limit_2fa_user_window_seconds: int = 300

    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "brandmarket"
    s3_region: str = "us-east-1"
    s3_use_ssl: bool = False
    # auto: S3, and the local folder while S3 is unreachable (old behaviour);
    # s3: S3 only, an upload fails if S3 is down; local: the local folder only.
    storage_backend: Literal["auto", "s3", "local"] = "auto"
    files_base_url: str = "http://localhost:9000/brandmarket"
    local_upload_dir: str = "./uploads"

    max_upload_size_mb: int = 50
    allowed_upload_mime_types: str = (
        "application/pdf,image/jpeg,image/png,image/webp,image/gif,"
        "video/mp4,video/webm,"
        "application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
        "application/zip,application/x-zip-compressed,application/octet-stream"
    )

    payment_provider: str = "mock"
    allow_mock_payments: bool | None = None
    alif_base_url: str = "https://test-web.alif.tj"
    alif_terminal_key: str | None = None
    alif_terminal_password: str | None = None
    alif_gate: str = "korti_milli"
    alif_currency: str = "TJS"
    alif_timeout_seconds: float = 15.0
    # Marketplace seller terminals (mpTerminalInfo). When both set, payments use Alif Marketplace API.
    alif_test_merchant_1: str | None = None
    alif_test_merchant_2: str | None = None
    public_api_url: str = "http://localhost:8080"
    frontend_url: str = "http://localhost:4000"

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = "Brand Market"
    smtp_use_tls: bool = True  # STARTTLS
    smtp_use_ssl: bool = False  # implicit SSL (port 465)
    smtp_timeout: int = 15
    password_reset_ttl_minutes: int = 60
    rate_limit_password_forgot_per_minute: int = 5
    rate_limit_password_reset_per_minute: int = 10

    @field_validator("business_timezone")
    @classmethod
    def _timezone_name(cls, value: str) -> str:
        # The name goes into SQL text (AT TIME ZONE), so only a plain zone name passes.
        name = (value or "").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_+\-]*(/[A-Za-z0-9_+\-]+){0,2}", name):
            raise ValueError("BUSINESS_TIMEZONE must be a zone name like Asia/Dushanbe")
        return name

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors(cls, v: str | List[str]) -> str:
        if isinstance(v, list):
            return ",".join(v)
        return v

    @field_validator(
        "smtp_host",
        "smtp_username",
        "smtp_password",
        "smtp_from_email",
        "smtp_from_name",
        "alif_terminal_key",
        "alif_terminal_password",
        "alif_test_merchant_1",
        "alif_test_merchant_2",
        "alif_base_url",
        "alif_gate",
        mode="before",
    )
    @classmethod
    def strip_secrets(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().strip("\"'").strip()
        return v

    @model_validator(mode="after")
    def normalize_gmail_app_password(self) -> "Settings":
        # Google shows app passwords as "abcd efgh ijkl mnop"; SMTP expects them without spaces.
        if self.smtp_host.lower() == "smtp.gmail.com":
            self.smtp_password = self.smtp_password.replace(" ", "")
        return self

    @property
    def is_dev_env(self) -> bool:
        return self.app_env.strip().lower() in DEV_ENVS

    @model_validator(mode="after")
    def require_strong_secret_outside_dev(self) -> "Settings":
        # A known/short key lets anyone forge JWTs of any user (incl. superadmin).
        if not self.is_dev_env and (
            self.secret_key == DEFAULT_SECRET_KEY or len(self.secret_key) < 32
        ):
            raise ValueError(
                "SECRET_KEY must be set to a random value of at least 32 characters "
                f"when APP_ENV={self.app_env!r}"
            )
        return self

    @model_validator(mode="after")
    def default_allow_mock_payments(self) -> "Settings":
        if self.allow_mock_payments is None:
            self.allow_mock_payments = self.app_env.strip().lower() == "development"
        return self

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_mime_types_list(self) -> List[str]:
        return [m.strip() for m in self.allowed_upload_mime_types.split(",") if m.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def smtp_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_from_email)

    @property
    def alif_requested(self) -> bool:
        return self.payment_provider.lower() == "alif"

    @property
    def alif_enabled(self) -> bool:
        return (
            self.alif_requested
            and bool(self.alif_terminal_key)
            and bool(self.alif_terminal_password)
        )

    @property
    def alif_marketplace_enabled(self) -> bool:
        return (
            self.alif_enabled
            and bool(self.alif_test_merchant_1)
            and bool(self.alif_test_merchant_2)
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
