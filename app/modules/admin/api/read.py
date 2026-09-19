from typing import Annotated, Literal

from fastapi import Depends, Query
from fastapi.responses import Response

from app.modules.admin.api.common import AdminService, router
from app.modules.admin.schemas import (
    AdminCatalogDetail,
    AdminCatalogListResponse,
    AdminCompaniesResponse,
    AdminCompanyDetail,
    AdminContractDetail,
    AdminContractListResponse,
    AdminDashboardResponse,
    AdminDisputeDetail,
    AdminDisputeListResponse,
    AdminFinanceDetail,
    AdminFinanceListResponse,
    AdminProposalDetail,
    AdminProposalListResponse,
    AdminReportDetail,
    AdminReportListResponse,
    AdminRfqDetail,
    AdminRfqListResponse,
    AdminUsersResponse,
)


@router.get("/dashboard", response_model=AdminDashboardResponse)
async def admin_dashboard():
    return await AdminService().get_dashboard()


@router.get("/users", response_model=AdminUsersResponse)
async def admin_list_users(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Literal["active", "blocked", "pending"] | None = None,
    query: Annotated[str | None, Query(max_length=255)] = None,
):
    return await AdminService().list_users(
        page=page,
        page_size=page_size,
        status=status,
        query=query,
    )


@router.get("/companies", response_model=AdminCompaniesResponse)
async def admin_list_companies(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Literal["all", "verified", "pending", "rejected", "blocked"] = "all",
    query: Annotated[str | None, Query(max_length=200)] = None,
):
    return await AdminService().list_companies(
        page=page,
        page_size=page_size,
        status=status,
        query=query,
    )


@router.get("/companies/pending-verification")
async def admin_pending_verification():
    return await AdminService().list_pending_verification()


@router.get("/companies/{company_id}", response_model=AdminCompanyDetail)
async def admin_get_company(company_id: int):
    return await AdminService().get_company_detail(company_id)


@router.get("/catalog", response_model=AdminCatalogListResponse)
async def admin_list_catalog(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    view: Literal["all", "products", "services", "draft", "reported", "hidden"] = "all",
    query: Annotated[str | None, Query(max_length=200)] = None,
):
    return await AdminService().list_catalog_items(
        page=page,
        page_size=page_size,
        view=view,
        query=query,
    )


@router.get("/catalog/{item_id}", response_model=AdminCatalogDetail)
async def admin_get_catalog_item(item_id: int):
    return await AdminService().get_catalog_item_detail(item_id)


@router.get("/rfqs", response_model=AdminRfqListResponse)
async def admin_list_rfqs(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    view: Literal["published", "closed", "draft", "reported", "archived"] = "published",
    query: Annotated[str | None, Query(max_length=200)] = None,
):
    return await AdminService().list_rfqs(
        page=page,
        page_size=page_size,
        view=view,
        query=query,
    )


@router.get("/rfqs/{rfq_id}", response_model=AdminRfqDetail)
async def admin_get_rfq(rfq_id: str):
    return await AdminService().get_rfq_detail(rfq_id)


@router.get("/proposals", response_model=AdminProposalListResponse)
async def admin_list_proposals(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    view: Literal["all", "pending", "accepted", "rejected", "reported"] = "all",
    query: Annotated[str | None, Query(max_length=200)] = None,
):
    return await AdminService().list_proposals(
        page=page,
        page_size=page_size,
        view=view,
        query=query,
    )


@router.get("/proposals/{proposal_id}", response_model=AdminProposalDetail)
async def admin_get_proposal(proposal_id: int):
    return await AdminService().get_proposal_detail(proposal_id)


@router.get("/contracts", response_model=AdminContractListResponse)
async def admin_list_contracts(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    view: Literal["active", "completed", "cancelled", "disputed"] = "active",
    query: Annotated[str | None, Query(max_length=255)] = None,
):
    return await AdminService().list_contracts(
        page=page,
        page_size=page_size,
        view=view,
        query=query,
    )


@router.get("/contracts/{contract_id}", response_model=AdminContractDetail)
async def admin_get_contract(contract_id: int):
    return await AdminService().get_contract_detail(contract_id)


@router.get("/disputes", response_model=AdminDisputeListResponse)
async def admin_list_disputes(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    view: Literal["open", "under_review", "resolved", "appealed"] = "open",
    query: Annotated[str | None, Query(max_length=255)] = None,
):
    return await AdminService().list_disputes(
        page=page,
        page_size=page_size,
        view=view,
        query=query,
    )


@router.get("/disputes/{dispute_id}", response_model=AdminDisputeDetail)
async def admin_get_dispute(dispute_id: int):
    return await AdminService().get_dispute_detail(dispute_id)


@router.get("/finance", response_model=AdminFinanceListResponse)
async def admin_list_finance(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    view: Literal[
        "platform_revenue",
        "subscriptions",
        "commission",
        "refunds",
        "payouts",
    ] = "platform_revenue",
    query: Annotated[str | None, Query(max_length=255)] = None,
):
    return await AdminService().list_finance(
        page=page,
        page_size=page_size,
        view=view,
        query=query,
    )


@router.get("/finance/export")
async def admin_export_finance(
    view: Literal[
        "platform_revenue",
        "subscriptions",
        "commission",
        "refunds",
        "payouts",
    ] = "platform_revenue",
    query: Annotated[str | None, Query(max_length=255)] = None,
    payment_id: Annotated[int | None, Query(ge=1)] = None,
):
    csv_body = await AdminService().export_finance_csv(
        view=view,
        query=query,
        payment_id=payment_id,
    )
    filename = f"finance-{payment_id}.csv" if payment_id else f"finance-{view}.csv"
    return Response(
        content=csv_body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/finance/{payment_id}", response_model=AdminFinanceDetail)
async def admin_get_finance(payment_id: int):
    return await AdminService().get_finance_detail(payment_id)


@router.get("/reports", response_model=AdminReportListResponse)
async def admin_list_reports(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    view: Literal["all", "spam", "fraud", "counterfeit", "abuse", "other"] = "all",
    query: Annotated[str | None, Query(max_length=255)] = None,
):
    return await AdminService().list_reports(
        page=page,
        page_size=page_size,
        view=view,
        query=query,
    )


@router.get("/reports/{target_type}/{report_id}", response_model=AdminReportDetail)
async def admin_get_report(
    target_type: Literal["catalog", "rfq", "proposal"],
    report_id: int,
):
    return await AdminService().get_report_detail(target_type, report_id)
