from typing import Annotated

from fastapi import Depends

from app.deps import AuthContext, CurrentUser
from app.modules.auth.api.common import (
    AuthService,
    MeResponse,
    get_auth_context,
    get_current_user,
    router,
)


@router.get("/me", response_model=MeResponse)
async def me(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
):
    active = ctx.actor.id if ctx.actor else None
    return await AuthService().get_me(user.id, active)
