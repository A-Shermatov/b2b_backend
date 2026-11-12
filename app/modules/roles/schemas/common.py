from pydantic import BaseModel, Field


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    is_active: bool | None = None


class RoleResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    is_system: bool
    is_active: bool
    users_count: int = 0


class PermissionActionCell(BaseModel):
    permission_id: int | None = None
    code: str | None = None
    enabled: bool = False
    #: A ticked box that does not cover everything: the actions the role still
    #: lacks («блокировка»). A box covers several actions; a built-in role may
    #: hold only some of them.
    missing: list[str] = []


class PermissionMatrixRow(BaseModel):
    resource: str
    alias_ru: str
    read: PermissionActionCell
    write: PermissionActionCell
    delete: PermissionActionCell


class RolePermissionMatrixResponse(BaseModel):
    role_id: int
    role_name: str
    rows: list[PermissionMatrixRow]


class RolePermissionMatrixUpdate(BaseModel):
    permission_ids: list[int] = Field(default_factory=list)


class AssignRoleRequest(BaseModel):
    user_id: int
    role_id: int


class PermissionCatalogResponse(BaseModel):
    rows: list[PermissionMatrixRow]
