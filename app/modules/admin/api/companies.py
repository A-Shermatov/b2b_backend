from typing import Annotated

from fastapi import Depends, File, Form, UploadFile

from app.core.exceptions import ValidationError
from app.deps import CurrentUser, admin_perm
from app.modules.admin.api.common import AdminService, router
from app.modules.admin.schemas import (
    AdminCompanyCategoriesUpdate,
    AdminCompanyDetail,
    AdminCompanyLogoResponse,
    AdminCompanyMemberAdd,
    AdminCompanyMemberUpdate,
    AdminCompanyOwnerChange,
    AdminCompanyUpdate,
    AdminCompanyVerificationSet,
)
from app.utils.storage import storage_service

CompanyEditor = Annotated[CurrentUser, Depends(admin_perm("admin_companies.update"))]


async def _company_owner_id(company_id: int) -> int:
    return await AdminService().get_company_owner_id(company_id)


@router.patch("/companies/{company_id}", response_model=AdminCompanyDetail)
async def admin_update_company(
    company_id: int,
    data: AdminCompanyUpdate,
    current_user: CompanyEditor,
):
    return await AdminService().update_company_admin(
        company_id, data.model_dump(exclude_unset=True), current_user
    )


@router.post("/companies/{company_id}/logo", response_model=AdminCompanyLogoResponse)
async def admin_upload_company_logo(
    company_id: int,
    current_user: CompanyEditor,
    file: UploadFile = File(...),
):
    owner_id = await _company_owner_id(company_id)
    key, _ = await storage_service.upload(file, f"logos/{owner_id}")
    return await AdminService().set_company_logo_admin(company_id, key, current_user)


@router.post("/companies/{company_id}/members", response_model=AdminCompanyDetail)
async def admin_add_company_member(
    company_id: int,
    data: AdminCompanyMemberAdd,
    current_user: CompanyEditor,
):
    return await AdminService().add_company_member_admin(
        company_id, data.email, data.role, current_user
    )


@router.patch("/companies/{company_id}/members/{user_id}", response_model=AdminCompanyDetail)
async def admin_update_company_member(
    company_id: int,
    user_id: int,
    data: AdminCompanyMemberUpdate,
    current_user: CompanyEditor,
):
    return await AdminService().update_company_member_admin(
        company_id, user_id, data.role, current_user
    )


@router.delete("/companies/{company_id}/members/{user_id}", response_model=AdminCompanyDetail)
async def admin_remove_company_member(
    company_id: int,
    user_id: int,
    current_user: CompanyEditor,
):
    return await AdminService().remove_company_member_admin(company_id, user_id, current_user)


@router.post("/companies/{company_id}/owner", response_model=AdminCompanyDetail)
async def admin_change_company_owner(
    company_id: int,
    data: AdminCompanyOwnerChange,
    current_user: CompanyEditor,
):
    return await AdminService().change_company_owner_admin(
        company_id,
        current_user,
        user_id=data.user_id,
        email=data.email,
        reason=data.reason,
    )


@router.put("/companies/{company_id}/categories", response_model=AdminCompanyDetail)
async def admin_set_company_categories(
    company_id: int,
    data: AdminCompanyCategoriesUpdate,
    current_user: CompanyEditor,
):
    return await AdminService().set_company_categories_admin(
        company_id, data.category_ids, current_user
    )


@router.post("/companies/{company_id}/documents", response_model=AdminCompanyDetail)
async def admin_add_company_document(
    company_id: int,
    current_user: CompanyEditor,
    file: UploadFile = File(...),
    title: str = Form(..., max_length=255),
    issuer: str = Form(..., max_length=255),
    issue_date: str = Form(..., max_length=20),
    expiry_date: str | None = Form(None, max_length=20),
):
    if not title.strip() or not issuer.strip() or not issue_date.strip():
        raise ValidationError("Заполните название, издателя и дату выдачи")
    owner_id = await _company_owner_id(company_id)
    key, _ = await storage_service.upload(file, f"certificates/{owner_id}")
    return await AdminService().add_company_document_admin(
        company_id,
        current_user,
        title=title.strip(),
        issuer=issuer.strip(),
        issue_date=issue_date.strip(),
        expiry_date=(expiry_date or "").strip() or None,
        file_key=key,
    )


@router.delete(
    "/companies/{company_id}/documents/{document_id}",
    response_model=AdminCompanyDetail,
)
async def admin_delete_company_document(
    company_id: int,
    document_id: int,
    current_user: CompanyEditor,
):
    return await AdminService().delete_company_document_admin(
        company_id, document_id, current_user
    )


@router.post("/companies/{company_id}/verification-status", response_model=AdminCompanyDetail)
async def admin_set_company_verification(
    company_id: int,
    data: AdminCompanyVerificationSet,
    current_user: CompanyEditor,
):
    return await AdminService().set_company_verification_admin(
        company_id, data.status, current_user, reason=data.reason
    )
