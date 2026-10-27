from app.modules.reviews.schemas import ReviewResponse

REVIEW_COLUMNS = """
    id,
    contract_id,
    reviewer_actor_id,
    target_actor_id,
    rating,
    service_rating,
    delivery_rating,
    quality_rating,
    COALESCE(tags, '{}'::text[]) AS tags,
    comment,
    created_at
"""


def shape_review_row(row) -> ReviewResponse:
    payload = dict(row)
    payload["tags"] = list(payload.get("tags") or [])
    return ReviewResponse.model_validate(payload)
