from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "BrandMarket API"
    app_env: str = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"

    secret_key: str = "change-me-to-a-long-random-secret-key-in-production"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7
    bcrypt_rounds: int = 12
    algorithm: str = "HS256"

    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    database_url: str
    redis_url: str | None = None
    rate_limit_login_per_minute: int = 5

    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "brandmarket"
    s3_region: str = "us-east-1"
    s3_use_ssl: bool = False
    files_base_url: str = "http://localhost:9000/brandmarket"
    local_upload_dir: str = "./uploads"

    max_upload_size_mb: int = 50
    allowed_upload_mime_types: str = (
        "application/pdf,image/jpeg,image/png,image/webp,image/gif,"
        "video/mp4,video/webm,"
        "application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
        "application/zip,application/x-zip-compressed,application/octet-stream"
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors(cls, v: str | List[str]) -> str:
        if isinstance(v, list):
            return ",".join(v)
        return v

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_mime_types_list(self) -> List[str]:
        return [m.strip() for m in self.allowed_upload_mime_types.split(",") if m.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
