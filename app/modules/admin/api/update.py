from typing import Annotated, Literal

from fastapi import Depends

from app.deps import require_admin
from app.modules.admin.api.common import AdminService, router
from app.modules.admin.schemas import (
    CatalogAdminActionRequest,
    CatalogAdminActionResponse,
    CompanyAdminActionRequest,
    CompanyAdminActionResponse,
    ContractAdminActionRequest,
    ContractAdminActionResponse,
    DisputeAdminActionRequest,
    DisputeAdminActionResponse,
    FinanceAdminActionRequest,
    FinanceAdminActionResponse,
    ProposalAdminActionRequest,
    ProposalAdminActionResponse,
    ReportAdminActionRequest,
    ReportAdminActionResponse,
    RfqAdminActionRequest,
    RfqAdminActionResponse,
    UserStatusUpdate,
    VerifyCompanyRequest,
)
from app.deps import CurrentUser
from app.modules.auth.schemas import UserPublic


@router.patch("/users/{user_id}/status", response_model=UserPublic)
async def admin_update_user_status(
    user_id: int,
    data: UserStatusUpdate,
    current_user: Annotated[CurrentUser, Depends(require_admin)],
):
    return await AdminService().update_user_status(
        user_id,
        data.status,
        current_user,
    )


@router.post("/companies/{company_id}/action", response_model=CompanyAdminActionResponse)
async def admin_apply_company_action(
    company_id: int,
    data: CompanyAdminActionRequest,
    current_user: Annotated[CurrentUser, Depends(require_admin)],
):
    return await AdminService().apply_company_action(
        company_id=company_id,
        action=data.action,
        current_user=current_user,
        reason=data.reason,
    )


@router.post("/companies/{company_id}/verify")
async def admin_verify_company(
    company_id: int,
    data: VerifyCompanyRequest,
    current_user: Annotated[CurrentUser, Depends(require_admin)],
):
    return await AdminService().apply_company_action(
        company_id=company_id,
        action="approve" if data.approved else "reject",
        current_user=current_user,
        reason=None if data.approved else "Legacy verification endpoint",
    )


@router.post("/catalog/{item_id}/action", response_model=CatalogAdminActionResponse)
async def admin_apply_catalog_action(
    item_id: int,
    data: CatalogAdminActionRequest,
    current_user: Annotated[CurrentUser, Depends(require_admin)],
):
    return await AdminService().apply_catalog_action(
        item_id=item_id,
        action=data.action,
        current_user=current_user,
        reason=data.reason,
    )


@router.post("/rfqs/{rfq_id}/action", response_model=RfqAdminActionResponse)
async def admin_apply_rfq_action(
    rfq_id: str,
    data: RfqAdminActionRequest,
    current_user: Annotated[CurrentUser, Depends(require_admin)],
):
    return await AdminService().apply_rfq_action(
        rfq_id=rfq_id,
        action=data.action,
        current_user=current_user,
        reason=data.reason,
    )


@router.post("/proposals/{proposal_id}/action", response_model=ProposalAdminActionResponse)
async def admin_apply_proposal_action(
    proposal_id: int,
    data: ProposalAdminActionRequest,
    current_user: Annotated[CurrentUser, Depends(require_admin)],
):
    return await AdminService().apply_proposal_action(
        proposal_id=proposal_id,
        action=data.action,
        current_user=current_user,
        reason=data.reason,
    )


@router.post("/contracts/{contract_id}/action", response_model=ContractAdminActionResponse)
async def admin_apply_contract_action(
    contract_id: int,
    data: ContractAdminActionRequest,
    current_user: Annotated[CurrentUser, Depends(require_admin)],
):
    return await AdminService().apply_contract_action(
        contract_id=contract_id,
        action=data.action,
        current_user=current_user,
        reason=data.reason,
    )


@router.post("/disputes/{dispute_id}/action", response_model=DisputeAdminActionResponse)
async def admin_apply_dispute_action(
    dispute_id: int,
    data: DisputeAdminActionRequest,
    current_user: Annotated[CurrentUser, Depends(require_admin)],
):
    return await AdminService().apply_dispute_action(
        dispute_id=dispute_id,
        action=data.action,
        current_user=current_user,
        reason=data.reason,
        partial_buyer_amount=data.partial_buyer_amount,
    )


@router.post("/finance/{payment_id}/action", response_model=FinanceAdminActionResponse)
async def admin_apply_finance_action(
    payment_id: int,
    data: FinanceAdminActionRequest,
    current_user: Annotated[CurrentUser, Depends(require_admin)],
):
    return await AdminService().apply_finance_action(
        payment_id=payment_id,
        action=data.action,
        current_user=current_user,
        reason=data.reason,
    )


@router.post(
    "/reports/{target_type}/{report_id}/action",
    response_model=ReportAdminActionResponse,
)
async def admin_apply_report_action(
    target_type: Literal["catalog", "rfq", "proposal"],
    report_id: int,
    data: ReportAdminActionRequest,
    current_user: Annotated[CurrentUser, Depends(require_admin)],
):
    return await AdminService().apply_report_action(
        target_type=target_type,
        report_id=report_id,
        action=data.action,
        current_user=current_user,
        reason=data.reason,
    )
