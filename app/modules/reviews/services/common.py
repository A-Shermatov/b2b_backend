from app.modules.reviews.schemas import ReviewMediaSchema, ReviewResponse

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
    created_at,
    COALESCE(helpful_count, 0) AS helpful_count
"""


async def load_review_photos(conn, review_ids: list[int]) -> dict[int, list[ReviewMediaSchema]]:
    if not review_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT id, review_id, file_name, file_url, sort_order
        FROM review_media
        WHERE review_id = ANY($1::int[])
        ORDER BY sort_order ASC, id ASC
        """,
        review_ids,
    )
    result: dict[int, list[ReviewMediaSchema]] = {rid: [] for rid in review_ids}
    for row in rows:
        result[row["review_id"]].append(
            ReviewMediaSchema(
                id=row["id"],
                file_name=row["file_name"],
                file_url=row["file_url"],
                sort_order=row["sort_order"],
            )
        )
    return result


async def load_viewer_votes(
    conn, review_ids: list[int], viewer_actor_id: int | None
) -> set[int]:
    if not review_ids or not viewer_actor_id:
        return set()
    rows = await conn.fetch(
        """
        SELECT review_id FROM review_helpful_votes
        WHERE voter_actor_id = $1 AND review_id = ANY($2::int[])
        """,
        viewer_actor_id,
        review_ids,
    )
    return {row["review_id"] for row in rows}


def shape_review_row(
    row,
    *,
    photos: list[ReviewMediaSchema] | None = None,
    viewer_voted: bool = False,
) -> ReviewResponse:
    payload = dict(row)
    payload["tags"] = list(payload.get("tags") or [])
    payload["photos"] = photos or []
    payload["helpful_count"] = int(payload.get("helpful_count") or 0)
    payload["viewer_voted"] = viewer_voted
    return ReviewResponse.model_validate(payload)


async def shape_review_rows(
    conn, rows, viewer_actor_id: int | None = None
) -> list[ReviewResponse]:
    ids = [row["id"] for row in rows]
    photos_map = await load_review_photos(conn, ids)
    voted = await load_viewer_votes(conn, ids, viewer_actor_id)
    return [
        shape_review_row(
            row,
            photos=photos_map.get(row["id"], []),
            viewer_voted=row["id"] in voted,
        )
        for row in rows
    ]
