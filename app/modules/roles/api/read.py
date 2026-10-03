from typing import Annotated

from fastapi import Depends

from app.deps import CurrentUser
from app.modules.roles.api.common import require_roles_manage, require_roles_view, router
from app.modules.roles.schemas import PermissionCatalogResponse, RolePermissionMatrixResponse, RoleResponse
from app.modules.roles.services import RoleService


@router.get("/roles", response_model=list[RoleResponse])
async def list_roles(_: Annotated[CurrentUser, Depends(require_roles_view)]):
    return await RoleService().list_roles()


@router.get("/roles/{role_id}", response_model=RoleResponse)
async def get_role(role_id: int, _: Annotated[CurrentUser, Depends(require_roles_view)]):
    return await RoleService().get_role(role_id)


@router.get("/permissions/matrix/catalog", response_model=PermissionCatalogResponse)
async def permission_catalog(_: Annotated[CurrentUser, Depends(require_roles_manage)]):
    return PermissionCatalogResponse(rows=await RoleService().catalog())


@router.get("/roles/{role_id}/matrix", response_model=RolePermissionMatrixResponse)
async def get_role_matrix(role_id: int, _: Annotated[CurrentUser, Depends(require_roles_manage)]):
    return await RoleService().role_matrix(role_id)
