from fastapi import APIRouter, Depends

from src.core.deps import require_buyer_ctx
from src.api.v1.buyer import (
    companies,
    contracts,
    notifications,
    payments,
    proposals,
    rfqs,
)

router = APIRouter(prefix="/buyer", dependencies=[Depends(require_buyer_ctx)])

router.include_router(companies.router)
router.include_router(rfqs.router)
router.include_router(proposals.router)
router.include_router(contracts.router)
router.include_router(payments.router)
router.include_router(notifications.router)
