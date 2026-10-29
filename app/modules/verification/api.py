from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.deps import CurrentUser, get_current_user
from app.modules.verification.common import ensure_identity_file_type
from app.modules.verification.schemas import IndividualDocumentType, IndividualVerificationState
from app.modules.verification.service import IndividualVerificationService
from app.utils.storage import storage_service

router = APIRouter(prefix="/me/verification", tags=["verification"])

User = Annotated[CurrentUser, Depends(get_current_user)]


@router.get("", response_model=IndividualVerificationState)
async def get_my_verification(user: User):
    return await IndividualVerificationService().get_status(user)


@router.post("/documents", response_model=IndividualVerificationState)
async def upload_my_verification_document(
    user: User,
    file: UploadFile = File(...),
    doc_type: IndividualDocumentType = Form("other"),
):
    ensure_identity_file_type(file.content_type)
    # Validate state before touching storage so rejected uploads leave no orphans.
    await IndividualVerificationService().ensure_can_upload(user)
    key, _ = await storage_service.upload(file, f"identity/{user.id}")
    return await IndividualVerificationService().add_document(
        user, doc_type=doc_type, file_name=file.filename, file_key=key
    )


@router.delete("/documents/{document_id}", response_model=IndividualVerificationState)
async def delete_my_verification_document(document_id: int, user: User):
    return await IndividualVerificationService().delete_document(user, document_id)


@router.post("/submit", response_model=IndividualVerificationState)
async def submit_my_verification(user: User):
    return await IndividualVerificationService().submit(user)
