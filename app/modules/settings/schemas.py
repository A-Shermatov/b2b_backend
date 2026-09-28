from datetime import datetime

from pydantic import BaseModel, Field


class PlatformSettingsUpdate(BaseModel):
    commission_percent: float = Field(ge=0, le=100)
    commission_min: float = Field(ge=0, le=1_000_000_000)
    max_contract_amount: float | None = Field(default=None, gt=0, le=1_000_000_000_000)


class PublicPlatformSettings(BaseModel):
    commission_percent: float
    commission_min: float
    max_contract_amount: float | None
    currency: str


class AdminPlatformSettings(PublicPlatformSettings):
    updated_at: datetime | None = None
    updated_by_name: str | None = None
    can_update: bool = False
