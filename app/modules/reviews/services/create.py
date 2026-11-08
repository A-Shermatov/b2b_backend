import asyncpg

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.modules.reviews.schemas import ReviewCreate, ReviewResponse
from app.modules.reviews.services.common import REVIEW_COLUMNS, shape_review_rows
from app.utils.audit import log_event


class ReviewCreateMixin:
    async def create(
        self, reviewer_actor_id: int, data: ReviewCreate, author_user_id: int | None = None
    ) -> ReviewResponse:
        if len(data.photos) > 5:
            raise ValidationError("Можно прикрепить не более 5 фото")

        async with self.conn.transaction():
            contract = await self.conn.fetchrow(
                """
                SELECT c.id, c.buyer_actor_id, c.supplier_actor_id,
                       c.status::text AS status, rf.catalog_item_id
                FROM contracts c
                LEFT JOIN rfqs rf ON rf.id = c.rfq_id
                WHERE c.id = $1
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
                        tags, comment, created_at, author_user_id, catalog_item_id
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::text[], $9, NOW(), $10, $11)
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
                    author_user_id,
                    # Only the buyer's review rates the item that was bought.
                    contract["catalog_item_id"] if reviewer_actor_id == buyer_id else None,
                )
            except asyncpg.UniqueViolationError as exc:
                raise ConflictError("Review already exists") from exc

            for index, photo in enumerate(data.photos):
                await self.conn.execute(
                    """
                    INSERT INTO review_media (review_id, file_name, file_url, sort_order)
                    VALUES ($1, $2, $3, $4)
                    """,
                    row["id"],
                    photo.file_name[:255],
                    photo.file_url[:500],
                    index,
                )

            await self._update_actor_rating(data.target_actor_id)
            await log_event(
                self.conn,
                "review.create",
                "review",
                row["id"],
                actor_id=reviewer_actor_id,
                details={
                    "contract_id": data.contract_id,
                    "target_actor_id": data.target_actor_id,
                    "rating": data.rating,
                    "photos": len(data.photos),
                },
            )
            shaped = await shape_review_rows(self.conn, [row], reviewer_actor_id)
            return shaped[0]

    async def vote_helpful(self, review_id: int, voter_actor_id: int) -> ReviewResponse:
        async with self.conn.transaction():
            review = await self.conn.fetchrow(
                f"SELECT {REVIEW_COLUMNS} FROM reviews WHERE id = $1 FOR UPDATE",
                review_id,
            )
            if not review:
                raise NotFoundError("Review not found")
            if review["reviewer_actor_id"] == voter_actor_id:
                raise ForbiddenError("Нельзя голосовать за свой отзыв")

            inserted = await self.conn.fetchval(
                """
                INSERT INTO review_helpful_votes (review_id, voter_actor_id)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                RETURNING review_id
                """,
                review_id,
                voter_actor_id,
            )
            if inserted:
                await self.conn.execute(
                    """
                    UPDATE reviews
                    SET helpful_count = COALESCE(helpful_count, 0) + 1
                    WHERE id = $1
                    """,
                    review_id,
                )
            row = await self.conn.fetchrow(
                f"SELECT {REVIEW_COLUMNS} FROM reviews WHERE id = $1",
                review_id,
            )
            shaped = await shape_review_rows(self.conn, [row], voter_actor_id)
            return shaped[0]

    async def _update_actor_rating(self, target_actor_id: int) -> None:
        company_id = await self.conn.fetchval(
            "SELECT company_id FROM actors WHERE id = $1",
            target_actor_id,
        )
        if not company_id:
            return
        avg = await self.conn.fetchval(
            """
            SELECT AVG(r.rating)::float
            FROM reviews r
            JOIN actors a ON a.id = r.target_actor_id
            WHERE a.company_id = $1
              AND (
                  a.side::text = 'supplier'
                  OR NOT EXISTS (
                      SELECT 1 FROM actors s
                      WHERE s.company_id = $1 AND s.side::text = 'supplier'
                  )
              )
            """,
            company_id,
        )
        rating = round(float(avg or 0.0), 2)
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
