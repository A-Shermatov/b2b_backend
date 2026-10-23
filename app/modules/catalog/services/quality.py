"""Catalog card completeness score (0–100), aligned with frontend scoreCatalogItem."""


def catalog_item_completeness_percent(
    *,
    title: str | None,
    description: str | None,
    category_id: int | None,
    status: str | None,
    attributes_count: int,
    media: list[dict],
    pricing: dict | None,
) -> int:
    title_len = len((title or "").strip())
    desc_len = len((description or "").strip())
    photos = sum(1 for m in media if (m.get("media_type") or "") == "image")
    has_video = any((m.get("media_type") or "") == "video" for m in media)
    docs = sum(1 for m in media if (m.get("media_type") or "") == "document")
    price_ok = bool(
        pricing
        and (
            pricing.get("fixed_price")
            or pricing.get("hourly_rate")
            or pricing.get("monthly_rate")
            or (pricing.get("tiers") or [])
        )
    )

    total = 0
    if 20 <= title_len <= 120:
        total += 10
    if category_id:
        total += 10
    if photos >= 3:
        total += 15
    elif photos >= 1:
        total += 8
    if has_video:
        total += 5
    if attributes_count >= 5:
        total += 15
    elif attributes_count >= 2:
        total += 8
    if price_ok:
        total += 10
    if desc_len >= 150:
        total += 15
    elif desc_len >= 40:
        total += 8
    if docs > 0:
        total += 5
    if status == "active":
        total += 10
    return min(100, total)
