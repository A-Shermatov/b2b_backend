from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int


def total_pages_for(total: int, page_size: int) -> int:
    if total <= 0:
        return 0
    return (total + page_size - 1) // page_size


def build_page(items: list[Any], total: int, page: int, page_size: int) -> dict:
    counted = int(total or 0)
    return {
        "items": items,
        "total": counted,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages_for(counted, page_size),
    }


def offset_for(page: int, page_size: int) -> int:
    return max(0, (page - 1) * page_size)


async def fetch_page(conn, count_sql: str, rows_sql: str, params: list, page: int, page_size: int):
    total = int(await conn.fetchval(count_sql, *params) or 0)
    limit_at = len(params) + 1
    offset_at = len(params) + 2
    rows = await conn.fetch(
        f"{rows_sql} LIMIT ${limit_at} OFFSET ${offset_at}",
        *params,
        page_size,
        offset_for(page, page_size),
    )
    return total, rows
