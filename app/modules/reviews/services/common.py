from app.modules.reviews.schemas import (
    ReviewAuthorSchema,
    ReviewMediaSchema,
    ReviewPartySchema,
    ReviewResponse,
)

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
    COALESCE(helpful_count, 0) AS helpful_count,
    author_user_id
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


def _party_label(kind: str, display_name: str | None, company_title: str | None, user_name: str | None) -> str:
    if kind == "company":
        return (company_title or display_name or "").strip() or "Компания"
    name = (user_name or display_name or "").strip()
    return name or "Пользователь"


async def load_review_parties(conn, actor_ids: list[int]) -> dict[int, ReviewPartySchema]:
    unique_ids = sorted({int(actor_id) for actor_id in actor_ids if actor_id})
    if not unique_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT
            a.id AS actor_id,
            a.kind::text AS kind,
            a.display_name,
            c.title AS company_title,
            NULLIF(TRIM(CONCAT_WS(' ', au.first_name, au.last_name)), '') AS actor_user_name,
            NULLIF(TRIM(CONCAT_WS(' ', ou.first_name, ou.last_name)), '') AS owner_user_name,
            (
                SELECT NULLIF(TRIM(CONCAT_WS(' ', du.first_name, du.last_name)), '')
                FROM company_users cu
                JOIN users du ON du.id = cu.user_id
                WHERE cu.company_id = c.id
                ORDER BY CASE WHEN cu.role::text = 'director' THEN 0 ELSE 1 END, cu.id
                LIMIT 1
            ) AS director_user_name
        FROM actors a
        LEFT JOIN companies c ON c.id = a.company_id
        LEFT JOIN users au ON au.id = a.user_id
        LEFT JOIN users ou ON ou.id = c.owner_id
        WHERE a.id = ANY($1::int[])
        """,
        unique_ids,
    )
    parties: dict[int, ReviewPartySchema] = {}
    for row in rows:
        kind = row["kind"] or "individual"
        company_title = (row["company_title"] or "").strip() or None
        user_name = (
            row["actor_user_name"]
            or row["owner_user_name"]
            or row["director_user_name"]
            or None
        )
        if isinstance(user_name, str):
            user_name = user_name.strip() or None
        display_name = _party_label(kind, row["display_name"], company_title, user_name)
        parties[row["actor_id"]] = ReviewPartySchema(
            actor_id=row["actor_id"],
            kind=kind,
            display_name=display_name,
            company_title=company_title,
            user_name=user_name,
        )
    for actor_id in unique_ids:
        if actor_id not in parties:
            parties[actor_id] = ReviewPartySchema(
                actor_id=actor_id,
                kind="individual",
                display_name=f"Участник #{actor_id}",
                company_title=None,
                user_name=None,
            )
    return parties


async def load_review_authors(conn, rows) -> dict[int, ReviewAuthorSchema]:
    """The person who wrote each review: name, role in the reviewing company, company.

    Older reviews were saved without an author; they get no entry here and the
    page falls back to the company's name.
    """
    pairs = {
        (row["author_user_id"], row["reviewer_actor_id"])
        for row in rows
        if "author_user_id" in row.keys() and row["author_user_id"]
    }
    if not pairs:
        return {}
    found = await conn.fetch(
        """
        SELECT
            u.id AS user_id,
            a.id AS actor_id,
            NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.last_name)), '') AS name,
            cu.role::text AS role,
            COALESCE(NULLIF(c.title, ''), c.legal_name) AS company_title
        FROM unnest($1::int[], $2::int[]) AS p(user_id, actor_id)
        JOIN users u ON u.id = p.user_id
        JOIN actors a ON a.id = p.actor_id
        LEFT JOIN companies c ON c.id = a.company_id AND a.kind::text = 'company'
        LEFT JOIN company_users cu ON cu.company_id = c.id AND cu.user_id = u.id
        """,
        [user_id for user_id, _ in pairs],
        [actor_id for _, actor_id in pairs],
    )
    by_pair = {(r["user_id"], r["actor_id"]): r for r in found}
    authors: dict[int, ReviewAuthorSchema] = {}
    for row in rows:
        key = (row.get("author_user_id"), row["reviewer_actor_id"])
        hit = by_pair.get(key)
        if hit is None:
            continue
        authors[row["id"]] = ReviewAuthorSchema(
            user_id=hit["user_id"],
            name=hit["name"],
            role=hit["role"],
            company_title=hit["company_title"],
        )
    return authors


def shape_review_row(
    row,
    *,
    photos: list[ReviewMediaSchema] | None = None,
    viewer_voted: bool = False,
    reviewer: ReviewPartySchema | None = None,
    target: ReviewPartySchema | None = None,
    author: ReviewAuthorSchema | None = None,
) -> ReviewResponse:
    payload = dict(row)
    payload.pop("author_user_id", None)
    payload["author"] = author
    payload["tags"] = list(payload.get("tags") or [])
    payload["photos"] = photos or []
    payload["helpful_count"] = int(payload.get("helpful_count") or 0)
    payload["viewer_voted"] = viewer_voted
    payload["reviewer"] = reviewer
    payload["target"] = target
    return ReviewResponse.model_validate(payload)


async def shape_review_rows(
    conn, rows, viewer_actor_id: int | None = None
) -> list[ReviewResponse]:
    ids = [row["id"] for row in rows]
    photos_map = await load_review_photos(conn, ids)
    voted = await load_viewer_votes(conn, ids, viewer_actor_id)
    actor_ids = [row["reviewer_actor_id"] for row in rows] + [
        row["target_actor_id"] for row in rows
    ]
    parties = await load_review_parties(conn, actor_ids)
    authors = await load_review_authors(conn, rows)
    return [
        shape_review_row(
            row,
            photos=photos_map.get(row["id"], []),
            viewer_voted=row["id"] in voted,
            reviewer=parties.get(row["reviewer_actor_id"]),
            target=parties.get(row["target_actor_id"]),
            author=authors.get(row["id"]),
        )
        for row in rows
    ]
