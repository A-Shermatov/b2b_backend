import asyncpg

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.modules.reviews.schemas import ReviewCreate, ReviewResponse
from app.modules.reviews.services.common import REVIEW_COLUMNS, shape_review_row


class ReviewCreateMixin:
    async def create(self, reviewer_actor_id: int, data: ReviewCreate) -> ReviewResponse:
        async with self.conn.transaction():
            contract = await self.conn.fetchrow(
                """
                SELECT id, buyer_actor_id, supplier_actor_id, status::text AS status
                FROM contracts
                WHERE id = $1
                """,
                data.contract_id,
            )
            if not contract:
                raise NotFoundError("Contract not found")
            if contract["status"] != "completed":
                raise ConflictError("Contract must be completed")

            buyer_id = contract["buyer_actor_id"]
            supplier_id = contract["supplier_actor_id"]
            if reviewer_actor_id == buyer_id:
                if data.target_actor_id != supplier_id:
                    raise ForbiddenError("Buyer can only review the supplier")
            elif reviewer_actor_id == supplier_id:
                if data.target_actor_id != buyer_id:
                    raise ForbiddenError("Supplier can only review the buyer")
            else:
                raise ForbiddenError("Only contract parties can leave a review")

            existing = await self.conn.fetchrow(
                """
                SELECT id FROM reviews
                WHERE contract_id = $1 AND reviewer_actor_id = $2
                """,
                data.contract_id,
                reviewer_actor_id,
            )
            if existing:
                raise ConflictError("Review already exists")

            try:
                row = await self.conn.fetchrow(
                    f"""
                    INSERT INTO reviews (
                        contract_id, reviewer_actor_id, target_actor_id,
                        rating, service_rating, delivery_rating, quality_rating,
                        tags, comment, created_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::text[], $9, NOW())
                    RETURNING {REVIEW_COLUMNS}
                    """,
                    data.contract_id,
                    reviewer_actor_id,
                    data.target_actor_id,
                    data.rating,
                    data.service_rating,
                    data.delivery_rating,
                    data.quality_rating,
                    list(data.tags or []),
                    data.comment,
                )
            except asyncpg.UniqueViolationError as exc:
                raise ConflictError("Review already exists") from exc

            await self._update_actor_rating(data.target_actor_id)
            return shape_review_row(row)

    async def _update_actor_rating(self, target_actor_id: int) -> None:
        avg = await self.conn.fetchval(
            "SELECT AVG(rating)::float FROM reviews WHERE target_actor_id = $1",
            target_actor_id,
        )
        rating = round(float(avg or 0.0), 2)
        company_id = await self.conn.fetchval(
            "SELECT company_id FROM actors WHERE id = $1",
            target_actor_id,
        )
        if not company_id:
            return
        await self.conn.execute(
            "UPDATE companies SET rating = $1, updated_at = NOW() WHERE id = $2",
            rating,
            company_id,
        )
        await self.conn.execute(
            "UPDATE company_stats SET average_rating = $1 WHERE company_id = $2",
            rating,
            company_id,
        )
