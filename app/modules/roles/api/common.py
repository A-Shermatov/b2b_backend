from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.permissions import ensure_permission
from app.deps import CurrentUser, require_admin

router = APIRouter(prefix="/admin", tags=["roles"])


async def require_roles_manage(
    user: Annotated[CurrentUser, Depends(require_admin)],
) -> CurrentUser:
    ensure_permission(user, "roles.manage")
    return user
