from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.permissions import ensure_permission
from app.deps import CurrentUser, admin_perm, require_admin

router = APIRouter(prefix="/admin", tags=["roles"])

require_roles_view = admin_perm("roles.manage", "roles.view", "admin_staff.view")


async def require_roles_manage(
    user: Annotated[CurrentUser, Depends(require_admin)],
) -> CurrentUser:
    ensure_permission(user, "roles.manage")
    return user
