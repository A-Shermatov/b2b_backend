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
