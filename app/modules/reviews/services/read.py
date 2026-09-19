from app.modules.reviews.schemas import ReviewResponse
from app.modules.reviews.services.common import REVIEW_COLUMNS, shape_review_row


class ReviewReadMixin:
    async def list_for_company(self, company_id: int) -> list[ReviewResponse]:
        rows = await self.conn.fetch(
            f"""
            SELECT {REVIEW_COLUMNS}
            FROM reviews
            WHERE target_actor_id IN (
                SELECT id FROM actors WHERE company_id = $1
            )
            ORDER BY created_at DESC
            """,
            company_id,
        )
        return [shape_review_row(row) for row in rows]

    async def list_for_actor(self, actor_id: int) -> list[ReviewResponse]:
        rows = await self.conn.fetch(
            f"""
            SELECT {REVIEW_COLUMNS}
            FROM reviews
            WHERE target_actor_id = $1
            ORDER BY created_at DESC
            """,
            actor_id,
        )
        return [shape_review_row(row) for row in rows]

    async def list_for_reviewer(self, reviewer_actor_id: int) -> list[ReviewResponse]:
        rows = await self.conn.fetch(
            f"""
            SELECT {REVIEW_COLUMNS}
            FROM reviews
            WHERE reviewer_actor_id = $1
            ORDER BY created_at DESC
            """,
            reviewer_actor_id,
        )
        return [shape_review_row(row) for row in rows]
