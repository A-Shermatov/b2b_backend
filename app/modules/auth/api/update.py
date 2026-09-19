from typing import Annotated

from fastapi import Depends

from app.modules.auth.api.common import (
    ActivateRoleRequest,
    AuthService,
    MeResponse,
    SwitchActorRequest,
    SwitchCompanyRequest,
    UserPublic,
    UserUpdateRequest,
    get_current_user,
    router,
)
from app.deps import CurrentUser


@router.patch("/me", response_model=UserPublic)
async def update_me(
    data: UserUpdateRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    return await AuthService().update_me(user.id, data)


@router.post("/switch-actor", response_model=MeResponse)
async def switch_actor(
    data: SwitchActorRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    return await AuthService().switch_actor(user.id, data.actor_id)


@router.post("/switch-company", response_model=MeResponse)
async def switch_company(
    data: SwitchCompanyRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    return await AuthService().switch_company(user.id, data.company_id)


@router.post("/activate-role", response_model=MeResponse)
async def activate_role(
    data: ActivateRoleRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    return await AuthService().activate_role(user.id, data)
