from typing import Annotated

from fastapi import BackgroundTasks, Depends, File, UploadFile

from app.config import get_settings
from app.deps import CurrentUser, get_current_user
from app.modules.companies.api.common import (
    AddTeamMemberRequest,
    BuyerContext,
    CertificateCreateRequest,
    CompanyService,
    CompanyWizardInput,
    CompanyWithRelations,
    SupplierContext,
    buyer_router,
    invitations_router,
    public_router,
    require_buyer_ctx,
    require_supplier_ctx,
    supplier_router,
)
from app.modules.companies.schemas import (
    CompanyCertificateUploadResponse,
    CompanyInvitationSchema,
    CompanyLogoUploadResponse,
    InvitationAccepted,
    InvitationPreview,
    InvitationTokenRequest,
    MyInvitationSchema,
)
from app.utils.email import send_email
from app.utils.storage import storage_service


@buyer_router.post("/", response_model=CompanyWithRelations)
async def buyer_create_company(
    data: CompanyWizardInput,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await CompanyService().create_from_wizard(ctx.user, data, "buyer")


@supplier_router.post("/", response_model=CompanyWithRelations)
async def supplier_create_company(
    data: CompanyWizardInput,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    # The plan limit is checked inside create_from_wizard for every route.
    return await CompanyService().create_from_wizard(ctx.user, data, "supplier")


@buyer_router.post("/logo", response_model=CompanyLogoUploadResponse)
async def buyer_upload_logo(
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
    file: UploadFile = File(...),
):
    key, _ = await storage_service.upload(file, f"logos/{ctx.user.id}")
    return CompanyLogoUploadResponse(logo=key)


@supplier_router.post("/logo", response_model=CompanyLogoUploadResponse)
async def supplier_upload_logo(
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
    file: UploadFile = File(...),
):
    key, _ = await storage_service.upload(file, f"logos/{ctx.user.id}")
    return CompanyLogoUploadResponse(logo=key)


@buyer_router.post("/certificates/upload", response_model=CompanyCertificateUploadResponse)
async def buyer_upload_certificate_file(
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
    file: UploadFile = File(...),
):
    key, _ = await storage_service.upload(file, f"certificates/{ctx.user.id}")
    return CompanyCertificateUploadResponse(file_url=key)


@supplier_router.post("/certificates/upload", response_model=CompanyCertificateUploadResponse)
async def supplier_upload_certificate_file(
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
    file: UploadFile = File(...),
):
    key, _ = await storage_service.upload(file, f"certificates/{ctx.user.id}")
    return CompanyCertificateUploadResponse(file_url=key)


@buyer_router.post("/{company_id}/certificates")
async def buyer_add_certificate(
    company_id: int,
    data: CertificateCreateRequest,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    cert = await CompanyService().add_certificate(company_id, ctx.user.id, data)
    return {"id": cert["id"]}


@supplier_router.post("/{company_id}/certificates")
async def supplier_add_certificate(
    company_id: int,
    data: CertificateCreateRequest,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    cert = await CompanyService().add_certificate(company_id, ctx.user.id, data)
    return {"id": cert["id"]}


async def _invite(company_id: int, user_id: int, data, background: BackgroundTasks):
    invitation, email = await CompanyService().invite_team_member(company_id, user_id, data)
    return _queue_invitation_email(invitation, email, background)


async def _resend(company_id: int, user_id: int, invitation_id: int, background: BackgroundTasks):
    invitation, email = await CompanyService().resend_invitation(
        company_id, user_id, invitation_id
    )
    return _queue_invitation_email(invitation, email, background)


def _queue_invitation_email(invitation, email, background: BackgroundTasks):
    # The letter goes out when the platform's mail is set up; the link in the
    # answer works either way and can be handed over in a messenger.
    if get_settings().smtp_enabled:
        background.add_task(send_email, email.to, email.subject, email.text)
        invitation.email_sent = True
    return invitation


@buyer_router.post("/{company_id}/team", response_model=CompanyInvitationSchema)
async def buyer_invite_team_member(
    company_id: int,
    data: AddTeamMemberRequest,
    background: BackgroundTasks,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    """Invite a person to the team. They join after accepting the invitation."""
    return await _invite(company_id, ctx.user.id, data, background)


@supplier_router.post("/{company_id}/team", response_model=CompanyInvitationSchema)
async def supplier_invite_team_member(
    company_id: int,
    data: AddTeamMemberRequest,
    background: BackgroundTasks,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    """Invite a person to the team. They join after accepting the invitation."""
    return await _invite(company_id, ctx.user.id, data, background)


@buyer_router.get("/{company_id}/invitations", response_model=list[CompanyInvitationSchema])
async def buyer_list_invitations(
    company_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await CompanyService().list_invitations(company_id, ctx.user.id)


@supplier_router.get("/{company_id}/invitations", response_model=list[CompanyInvitationSchema])
async def supplier_list_invitations(
    company_id: int,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await CompanyService().list_invitations(company_id, ctx.user.id)


@buyer_router.post(
    "/{company_id}/invitations/{invitation_id}/resend",
    response_model=CompanyInvitationSchema,
)
async def buyer_resend_invitation(
    company_id: int,
    invitation_id: int,
    background: BackgroundTasks,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    return await _resend(company_id, ctx.user.id, invitation_id, background)


@supplier_router.post(
    "/{company_id}/invitations/{invitation_id}/resend",
    response_model=CompanyInvitationSchema,
)
async def supplier_resend_invitation(
    company_id: int,
    invitation_id: int,
    background: BackgroundTasks,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    return await _resend(company_id, ctx.user.id, invitation_id, background)


@buyer_router.delete("/{company_id}/invitations/{invitation_id}", status_code=204)
async def buyer_cancel_invitation(
    company_id: int,
    invitation_id: int,
    ctx: Annotated[BuyerContext, Depends(require_buyer_ctx)],
):
    await CompanyService().cancel_invitation(company_id, ctx.user.id, invitation_id)


@supplier_router.delete("/{company_id}/invitations/{invitation_id}", status_code=204)
async def supplier_cancel_invitation(
    company_id: int,
    invitation_id: int,
    ctx: Annotated[SupplierContext, Depends(require_supplier_ctx)],
):
    await CompanyService().cancel_invitation(company_id, ctx.user.id, invitation_id)


# --- the invited person: any signed-in user, whatever profile is active ---


@invitations_router.get("", response_model=list[MyInvitationSchema])
async def my_invitations(user: Annotated[CurrentUser, Depends(get_current_user)]):
    """Invitations waiting for my answer."""
    return await CompanyService().my_invitations(user.id)


@invitations_router.post("/accept", response_model=InvitationAccepted)
async def accept_invitation_by_link(
    data: InvitationTokenRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    company_id = await CompanyService().accept_invitation(user.id, token=data.token)
    return InvitationAccepted(company_id=company_id)


@invitations_router.post("/decline", status_code=204)
async def decline_invitation_by_link(
    data: InvitationTokenRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    await CompanyService().decline_invitation(user.id, token=data.token)


@invitations_router.post("/{invitation_id}/accept", response_model=InvitationAccepted)
async def accept_invitation(
    invitation_id: int,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    company_id = await CompanyService().accept_invitation(user.id, invitation_id=invitation_id)
    return InvitationAccepted(company_id=company_id)


@invitations_router.post("/{invitation_id}/decline", status_code=204)
async def decline_invitation(
    invitation_id: int,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    await CompanyService().decline_invitation(user.id, invitation_id=invitation_id)


@public_router.get("/invitations/{token}", response_model=InvitationPreview)
async def preview_invitation(token: str):
    """What the invitation link shows: the company, the access level, the address."""
    return await CompanyService().preview_invitation(token)
