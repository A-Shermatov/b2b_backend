"""Private files (identity documents, deal files, dispute evidence).

They are served only through short-lived signed links made by
``public_file_url``; the link itself is the permission, the same way S3
presigned URLs work, so ``<img src>`` and downloads keep working without
an Authorization header.
"""

import mimetypes

from fastapi import APIRouter, FastAPI, Query
from fastapi.responses import Response

from app.core.exceptions import NotFoundError
from app.utils.storage import (
    FILES_ROUTE,
    INLINE_EXTENSIONS,
    is_private_key,
    read_stored_file,
    verify_file_signature,
)

router = APIRouter(tags=["files"])


@router.get(FILES_ROUTE + "/{key:path}", include_in_schema=False)
async def get_private_file(
    key: str,
    exp: str | None = Query(default=None),
    sig: str | None = Query(default=None),
) -> Response:
    parts = key.replace("\\", "/").split("/")
    if (
        not is_private_key(key)
        or ".." in parts
        or not verify_file_signature(key, exp, sig)
    ):
        # The same answer for "no such file" and "bad link": nothing to probe.
        raise NotFoundError("Файл не найден или ссылка устарела")
    content = await read_stored_file(key)
    if content is None:
        raise NotFoundError("Файл не найден или ссылка устарела")

    dot = key.rfind(".")
    ext = key[dot:].lower() if dot != -1 else ""
    media_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Cache-Control": "private, max-age=300",
        "Referrer-Policy": "no-referrer",
    }
    if ext not in INLINE_EXTENSIONS:
        headers["Content-Disposition"] = "attachment"
        media_type = "application/octet-stream"
    return Response(content=content, media_type=media_type, headers=headers)


def register(app: FastAPI, prefix: str = "/api/v1") -> None:
    app.include_router(router, prefix=prefix)
