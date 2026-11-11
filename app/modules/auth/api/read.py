from typing import Annotated

from fastapi import Depends, Header

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
    x_actor_id: Annotated[int | None, Header(alias="X-Actor-Id")] = None,
    x_company_id: Annotated[int | None, Header(alias="X-Company-Id")] = None,
):
    # The profile the request named stays active. Without one (a fresh sign-in)
    # the service picks where the person starts: in their company or as themselves.
    named = x_actor_id is not None or x_company_id is not None
    active = ctx.actor.id if named and ctx.actor else None
    return await AuthService().get_me(user.id, active)
