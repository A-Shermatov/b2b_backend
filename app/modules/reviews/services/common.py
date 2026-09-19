from app.modules.reviews.schemas import ReviewResponse

REVIEW_COLUMNS = """
    id,
    contract_id,
    reviewer_actor_id,
    target_actor_id,
    rating,
    comment,
    created_at
"""


def shape_review_row(row) -> ReviewResponse:
    return ReviewResponse.model_validate(dict(row))
