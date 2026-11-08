from app.core.pagination import build_page, fetch_page
from app.modules.reviews.services.common import REVIEW_COLUMNS, shape_review_rows


class ReviewReadMixin:
    async def _page_reviews(
        self,
        where_sql: str,
        params: list,
        page: int,
        page_size: int,
        viewer_actor_id: int | None = None,
    ) -> dict:
        total, rows = await fetch_page(
            self.conn,
            f"SELECT COUNT(*) FROM reviews WHERE {where_sql}",
            f"""
            SELECT {REVIEW_COLUMNS}
            FROM reviews
            WHERE {where_sql}
            ORDER BY created_at DESC
            """,
            params,
            page,
            page_size,
        )
        items = await shape_review_rows(self.conn, rows, viewer_actor_id)
        return build_page(items, total, page, page_size)

    async def list_for_company(
        self,
        company_id: int,
        page: int = 1,
        page_size: int = 20,
        viewer_actor_id: int | None = None,
    ) -> dict:
        return await self._page_reviews(
            "target_actor_id IN (SELECT id FROM actors WHERE company_id = $1)",
            [company_id],
            page,
            page_size,
            viewer_actor_id,
        )

    async def list_for_actor(
        self,
        actor_id: int,
        page: int = 1,
        page_size: int = 20,
        viewer_actor_id: int | None = None,
    ) -> dict:
        return await self._page_reviews(
            "target_actor_id = $1",
            [actor_id],
            page,
            page_size,
            viewer_actor_id,
        )

    async def list_for_item(
        self,
        item_id: int,
        page: int = 1,
        page_size: int = 20,
        viewer_actor_id: int | None = None,
    ) -> dict:
        result = await self._page_reviews(
            "catalog_item_id = $1", [item_id], page, page_size, viewer_actor_id
        )
        stats = await self.conn.fetch(
            """
            SELECT rating, COUNT(*)::int AS n
            FROM reviews
            WHERE catalog_item_id = $1
            GROUP BY rating
            """,
            item_id,
        )
        distribution = {star: 0 for star in range(5, 0, -1)}
        for row in stats:
            distribution[int(row["rating"])] = row["n"]
        count = sum(distribution.values())
        average = (
            round(sum(star * n for star, n in distribution.items()) / count, 2) if count else 0.0
        )
        result["summary"] = {"average": average, "count": count, "distribution": distribution}
        return result

    async def item_review_eligibility(self, item_id: int, buyer_actor_id: int) -> dict:
        """The latest finished deal for this item that the buyer has not reviewed yet."""
        row = await self.conn.fetchrow(
            """
            SELECT c.id, c.supplier_actor_id,
                   EXISTS (
                       SELECT 1 FROM reviews r
                       WHERE r.contract_id = c.id AND r.reviewer_actor_id = $2
                   ) AS reviewed
            FROM contracts c
            JOIN rfqs rf ON rf.id = c.rfq_id
            WHERE rf.catalog_item_id = $1
              AND c.buyer_actor_id = $2
              AND c.status::text = 'completed'
            ORDER BY reviewed ASC, c.created_at DESC
            LIMIT 1
            """,
            item_id,
            buyer_actor_id,
        )
        if not row:
            return {"contract_id": None, "target_actor_id": None, "already_reviewed": False}
        if row["reviewed"]:
            return {"contract_id": None, "target_actor_id": None, "already_reviewed": True}
        return {
            "contract_id": row["id"],
            "target_actor_id": row["supplier_actor_id"],
            "already_reviewed": False,
        }

    async def list_for_reviewer(
        self,
        reviewer_actor_id: int,
        page: int = 1,
        page_size: int = 20,
        viewer_actor_id: int | None = None,
    ) -> dict:
        return await self._page_reviews(
            "reviewer_actor_id = $1",
            [reviewer_actor_id],
            page,
            page_size,
            viewer_actor_id or reviewer_actor_id,
        )
