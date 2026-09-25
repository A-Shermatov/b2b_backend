from typing import Annotated

from fastapi import Depends

from app.deps import CurrentUser
from app.modules.roles.api.common import require_roles_manage, router
from app.modules.roles.schemas import RolePermissionMatrixResponse, RolePermissionMatrixUpdate, RoleResponse, RoleUpdate
from app.modules.roles.services import RoleService


@router.put("/roles/{role_id}", response_model=RoleResponse)
async def update_role(
    role_id: int,
    data: RoleUpdate,
    _: Annotated[CurrentUser, Depends(require_roles_manage)],
):
    return await RoleService().update_role(role_id, data)


@router.put("/roles/{role_id}/matrix", response_model=RolePermissionMatrixResponse)
async def update_role_matrix(
    role_id: int,
    data: RolePermissionMatrixUpdate,
    user: Annotated[CurrentUser, Depends(require_roles_manage)],
):
    return await RoleService().replace_matrix(role_id, data.permission_ids, user.role_id)


@router.delete("/roles/{role_id}", status_code=204)
async def delete_role(
    role_id: int,
    _: Annotated[CurrentUser, Depends(require_roles_manage)],
):
    await RoleService().delete_role(role_id)
