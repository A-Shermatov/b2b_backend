from app.modules.catalog.schemas import CategoryTree


class CategoryReadMixin:
    async def get_tree(self) -> list[CategoryTree]:
        rows = await self.conn.fetch(
            """
            SELECT id, parent_id, name, slug
            FROM categories
            ORDER BY id
            """
        )
        by_parent: dict[int | None, list] = {}
        for row in rows:
            by_parent.setdefault(row["parent_id"], []).append(row)

        def build(parent_id: int | None) -> list[CategoryTree]:
            nodes = []
            for cat in by_parent.get(parent_id, []):
                nodes.append(
                    CategoryTree(
                        id=cat["id"],
                        parent_id=cat["parent_id"],
                        name=cat["name"],
                        slug=cat["slug"],
                        children=build(cat["id"]),
                    )
                )
            return nodes

        return build(None)
