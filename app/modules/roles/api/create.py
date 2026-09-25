from typing import Annotated

from fastapi import Depends

from app.deps import CurrentUser
from app.modules.roles.api.common import require_roles_manage, router
from app.modules.roles.schemas import AssignRoleRequest, RoleCreate, RoleResponse
from app.modules.roles.services import RoleService


@router.post("/roles", response_model=RoleResponse, status_code=201)
async def create_role(
    data: RoleCreate,
    _: Annotated[CurrentUser, Depends(require_roles_manage)],
):
    return await RoleService().create_role(data.name, data.description)


@router.post("/roles/assign", status_code=204)
async def assign_role(
    data: AssignRoleRequest,
    _: Annotated[CurrentUser, Depends(require_roles_manage)],
):
    await RoleService().assign_role(data.user_id, data.role_id)
