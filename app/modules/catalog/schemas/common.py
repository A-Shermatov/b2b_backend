from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.utils.storage import FileUrlMixin


class CategorySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    parent_id: int | None
    name: str
    slug: str


class CategoryTree(CategorySchema):
    children: list["CategoryTree"] = []


class ItemAttributeSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_id: int
    name: str
    value: str
    value_type: str
    sort_order: int


class ItemPricingTierSchema(BaseModel):
    min_qty: int
    price: float


class ItemPricingSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_id: int
    pricing_type: str
    currency: str
    fixed_price: float | None
    hourly_rate: float | None
    monthly_rate: float | None
    tiers: list[ItemPricingTierSchema] = []


class ItemMediaSchema(FileUrlMixin):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_id: int
    file_name: str
    file_url: str
    media_type: str
    sort_order: int


class ItemStatsSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    item_id: int
    views: int
    leads: int


class CategoryRefSchema(BaseModel):
    id: int
    parent_id: int | None
    name: str
    slug: str


class CatalogItemSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_id: int
    type: str
    category_id: int
    title: str
    description: str | None
    status: str
    created_at: datetime


class CatalogItemWithRelations(CatalogItemSchema):
    category: CategoryRefSchema | None = None
    attributes: list[ItemAttributeSchema] = []
    pricing: ItemPricingSchema | None = None
    media: list[ItemMediaSchema] = []
    stats: ItemStatsSchema | None = None
    rejection_reason: str | None = None
    completeness_percent: int = Field(ge=0, le=100, description="Заполненность карточки, 0–100")


class ItemAttributeInput(BaseModel):
    name: str
    value: str
    value_type: str = "text"
    sort_order: int = 0


class ItemPricingInput(BaseModel):
    pricing_type: str
    currency: str = "TJS"
    fixed_price: float | None = None
    hourly_rate: float | None = None
    monthly_rate: float | None = None
    tiers: list[ItemPricingTierSchema] = []


class ItemMediaInput(BaseModel):
    file_name: str
    file_url: str
    media_type: str
    sort_order: int = 0


class CatalogMediaUploadResponse(FileUrlMixin):
    file_name: str
    file_url: str
    media_type: str


class CatalogItemInput(BaseModel):
    type: str
    category_id: int
    title: str = Field(min_length=2)
    description: str = ""
    status: str = "draft"
    attributes: list[ItemAttributeInput] = []
    media: list[ItemMediaInput] = []
    pricing: ItemPricingInput


class CatalogItemReportCreate(BaseModel):
    reason: str = Field(pattern="^(spam|fraud|counterfeit|abuse|other)$")
    details: str | None = Field(default=None, max_length=2000)


class CatalogItemReportResponse(BaseModel):
    id: int
    item_id: int
    reason: str
    details: str | None
    status: str
    created_at: datetime
