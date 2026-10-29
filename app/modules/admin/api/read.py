from typing import Annotated, Literal

from fastapi import Depends, Query
from fastapi.responses import Response

from app.deps import PaginationDep, admin_perm
from app.modules.admin.api.common import AdminService, router
from app.modules.admin.schemas import (
    AdminCatalogDetail,
    AdminCatalogListResponse,
    AdminChatDetail,
    AdminChatListResponse,
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
    AdminUserDetail,
    AdminUsersResponse,
    CompanyChangeRequestAdminSchema,
)
from app.modules.companies.services import CompanyService


@router.get(
    "/dashboard",
    response_model=AdminDashboardResponse,
    dependencies=[Depends(admin_perm("admin_dashboard.view"))],
)
async def admin_dashboard():
    return await AdminService().get_dashboard()


@router.get(
    "/users",
    response_model=AdminUsersResponse,
    dependencies=[Depends(admin_perm("admin_users.view"))],
)
async def admin_list_users(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Literal["active", "blocked", "pending"] | None = None,
    role_side: Literal["buyer", "supplier"] | None = None,
    query: Annotated[str | None, Query(max_length=255)] = None,
):
    return await AdminService().list_users(
        page=page,
        page_size=page_size,
        status=status,
        role_side=role_side,
        query=query,
    )


@router.get(
    "/users/{user_id}",
    response_model=AdminUserDetail,
    dependencies=[Depends(admin_perm("admin_users.view"))],
)
async def admin_get_user(user_id: int):
    return await AdminService().get_user(user_id)


@router.get(
    "/companies",
    response_model=AdminCompaniesResponse,
    dependencies=[Depends(admin_perm("admin_companies.view"))],
)
async def admin_list_companies(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Literal["all", "verified", "pending", "rejected", "blocked"] = "all",
    query: Annotated[str | None, Query(max_length=200)] = None,
    actor_type: Literal["buyer", "supplier"] | None = None,
    verification_status: Literal["pending", "verified", "rejected", "needs_documents"]
    | None = None,
    operational_status: Literal["active", "blocked", "deactivated"] | None = None,
    vip: bool | None = None,
    sort: Literal["created_at", "title", "rating"] = "created_at",
    order: Literal["asc", "desc"] = "desc",
):
    return await AdminService().list_companies(
        page=page,
        page_size=page_size,
        status=status,
        query=query,
        actor_type=actor_type,
        verification_status=verification_status,
        operational_status=operational_status,
        vip=vip,
        sort=sort,
        order=order,
    )


@router.get(
    "/companies/pending-verification",
    dependencies=[Depends(admin_perm("admin_companies.view"))],
)
async def admin_pending_verification(pagination: PaginationDep):
    return await AdminService().list_pending_verification(pagination.page, pagination.page_size)


@router.get(
    "/companies/change-requests",
    dependencies=[Depends(admin_perm("admin_companies.view"))],
)
async def admin_list_change_requests(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Literal["pending", "approved", "rejected"] = "pending",
    query: Annotated[str | None, Query(max_length=200)] = None,
):
    return await CompanyService().admin_list_change_requests(
        status=status, page=page, page_size=page_size, query=query
    )


@router.get(
    "/companies/change-requests/{request_id}",
    response_model=CompanyChangeRequestAdminSchema,
    dependencies=[Depends(admin_perm("admin_companies.view"))],
)
async def admin_get_change_request(request_id: int):
    return await CompanyService().admin_get_change_request(request_id)


@router.get(
    "/companies/{company_id}",
    response_model=AdminCompanyDetail,
    dependencies=[Depends(admin_perm("admin_companies.view"))],
)
async def admin_get_company(company_id: int):
    return await AdminService().get_company_detail(company_id)


@router.get(
    "/catalog",
    response_model=AdminCatalogListResponse,
    dependencies=[Depends(admin_perm("admin_catalog.view"))],
)
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


@router.get(
    "/catalog/{item_id}",
    response_model=AdminCatalogDetail,
    dependencies=[Depends(admin_perm("admin_catalog.view"))],
)
async def admin_get_catalog_item(item_id: int):
    return await AdminService().get_catalog_item_detail(item_id)


@router.get(
    "/rfqs",
    response_model=AdminRfqListResponse,
    dependencies=[Depends(admin_perm("admin_rfqs.view"))],
)
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


@router.get(
    "/rfqs/{rfq_id}",
    response_model=AdminRfqDetail,
    dependencies=[Depends(admin_perm("admin_rfqs.view"))],
)
async def admin_get_rfq(rfq_id: str):
    return await AdminService().get_rfq_detail(rfq_id)


@router.get(
    "/proposals",
    response_model=AdminProposalListResponse,
    dependencies=[Depends(admin_perm("admin_proposals.view"))],
)
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


@router.get(
    "/proposals/{proposal_id}",
    response_model=AdminProposalDetail,
    dependencies=[Depends(admin_perm("admin_proposals.view"))],
)
async def admin_get_proposal(proposal_id: int):
    return await AdminService().get_proposal_detail(proposal_id)


@router.get(
    "/contracts",
    response_model=AdminContractListResponse,
    dependencies=[Depends(admin_perm("admin_contracts.view"))],
)
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


@router.get(
    "/contracts/{contract_id}",
    response_model=AdminContractDetail,
    dependencies=[Depends(admin_perm("admin_contracts.view"))],
)
async def admin_get_contract(contract_id: int):
    return await AdminService().get_contract_detail(contract_id)


@router.get(
    "/disputes",
    response_model=AdminDisputeListResponse,
    dependencies=[Depends(admin_perm("admin_disputes.view"))],
)
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


@router.get(
    "/disputes/{dispute_id}",
    response_model=AdminDisputeDetail,
    dependencies=[Depends(admin_perm("admin_disputes.view"))],
)
async def admin_get_dispute(dispute_id: int):
    return await AdminService().get_dispute_detail(dispute_id)


@router.get(
    "/finance",
    response_model=AdminFinanceListResponse,
    dependencies=[Depends(admin_perm("admin_finance.view"))],
)
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


@router.get(
    "/finance/export",
    dependencies=[Depends(admin_perm("admin_finance.view"))],
)
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


@router.get(
    "/finance/{payment_id}",
    response_model=AdminFinanceDetail,
    dependencies=[Depends(admin_perm("admin_finance.view"))],
)
async def admin_get_finance(payment_id: int):
    return await AdminService().get_finance_detail(payment_id)


@router.get(
    "/reports",
    response_model=AdminReportListResponse,
    dependencies=[Depends(admin_perm("admin_reports.view"))],
)
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


@router.get(
    "/reports/{target_type}/{report_id}",
    response_model=AdminReportDetail,
    dependencies=[Depends(admin_perm("admin_reports.view"))],
)
async def admin_get_report(
    target_type: Literal["catalog", "rfq", "proposal"],
    report_id: int,
):
    return await AdminService().get_report_detail(target_type, report_id)


@router.get(
    "/chats",
    response_model=AdminChatListResponse,
    dependencies=[Depends(admin_perm("admin_contracts.view", "admin_proposals.view"))],
)
async def admin_list_chats(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    filter: Literal["all", "flag", "disp"] = "all",
    query: Annotated[str | None, Query(max_length=255)] = None,
):
    return await AdminService().list_chats(
        filter_key=filter,
        query=query,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/chats/{chat_id}",
    response_model=AdminChatDetail,
    dependencies=[Depends(admin_perm("admin_contracts.view", "admin_proposals.view"))],
)
async def admin_get_chat(chat_id: str):
    return await AdminService().get_chat(chat_id)
