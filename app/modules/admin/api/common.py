from fastapi import APIRouter, Depends

from app.deps import require_admin
from app.modules.admin.services import AdminService

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])
