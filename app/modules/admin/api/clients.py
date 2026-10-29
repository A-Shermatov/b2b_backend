from typing import Annotated, Literal

from fastapi import Depends, File, Form, Query, UploadFile

from app.deps import CurrentUser, admin_perm
from app.modules.admin.api.common import AdminService, router
from app.modules.admin.schemas.clients import (
    AdminIndividualDetail,
    AdminIndividualsResponse,
    AdminIndividualVerificationSet,
    AdminUserDossier,
    AdminVerificationCounts,
)
from app.modules.verification.common import ensure_identity_file_type
from app.modules.verification.schemas import IndividualDocumentType
from app.utils.storage import storage_service

IndividualViewer = Annotated[
    CurrentUser,
    Depends(admin_perm("admin_companies.view", "admin_users.view")),
]
IndividualEditor = Annotated[
    CurrentUser,
    Depends(admin_perm("admin_companies.update", "admin_users.update")),
]


@router.get("/verification/counts", response_model=AdminVerificationCounts)
async def admin_verification_counts(_: IndividualViewer):
    return await AdminService().get_verification_counts()


@router.get("/individuals", response_model=AdminIndividualsResponse)
async def admin_list_individuals(
    _: IndividualViewer,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Literal[
        "queue", "all", "none", "pending", "needs_documents", "verified", "rejected"
    ] = "queue",
    side: Literal["buyer", "supplier"] | None = None,
    query: Annotated[str | None, Query(max_length=200)] = None,
):
    return await AdminService().list_individuals(
        page=page, page_size=page_size, status=status, side=side, query=query
    )


@router.get("/individuals/{user_id}", response_model=AdminIndividualDetail)
async def admin_get_individual(user_id: int, _: IndividualViewer):
    return await AdminService().get_individual(user_id)


@router.post("/individuals/{user_id}/verification", response_model=AdminIndividualDetail)
async def admin_set_individual_verification(
    user_id: int,
    data: AdminIndividualVerificationSet,
    current_user: IndividualEditor,
):
    return await AdminService().set_individual_verification_admin(
        user_id, data.action, current_user, status=data.status, reason=data.reason
    )


@router.post("/individuals/{user_id}/documents", response_model=AdminIndividualDetail)
async def admin_add_individual_document(
    user_id: int,
    current_user: IndividualEditor,
    file: UploadFile = File(...),
    doc_type: IndividualDocumentType = Form("other"),
):
    ensure_identity_file_type(file.content_type)
    await AdminService().get_individual(user_id)
    key, _ = await storage_service.upload(file, f"identity/{user_id}")
    return await AdminService().add_individual_document_admin(
        user_id, current_user, doc_type=doc_type, file_name=file.filename, file_key=key
    )


@router.delete(
    "/individuals/{user_id}/documents/{document_id}", response_model=AdminIndividualDetail
)
async def admin_delete_individual_document(
    user_id: int,
    document_id: int,
    current_user: IndividualEditor,
):
    return await AdminService().delete_individual_document_admin(
        user_id, document_id, current_user
    )


@router.get(
    "/users/{user_id}/dossier",
    response_model=AdminUserDossier,
    dependencies=[Depends(admin_perm("admin_users.view"))],
)
async def admin_get_user_dossier(user_id: int):
    return await AdminService().get_user_dossier(user_id)
