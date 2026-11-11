import logging

from fastapi import APIRouter, Request

from app.core.exceptions import RateLimitError
from app.core.middleware.http_audit import facts_of
from app.modules.activity import service
from app.modules.activity.schemas import ActivityAccepted, ActivityBatch
from app.utils import redis_client
from app.utils.audit import request_facts
from app.utils.request_meta import parse_optional_int

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/activity", tags=["activity"])

# Anybody may send a report, so how much one browser and one address can write
# to the journal is bounded. A person opens a page every few seconds at most; an
# office behind one address is many such people.
REPORTS_PER_DEVICE_PER_MINUTE = 30
REPORTS_PER_ADDRESS_PER_MINUTE = 180


@router.post("", response_model=ActivityAccepted)
async def report_activity(batch: ActivityBatch, request: Request):
    """The site's own report: the pages a person opened and what their browser is.

    Open to guests as well: a visitor who has not signed in still has a device,
    and what they looked at before signing up is part of the picture. The person,
    when there is one, comes from the access token; nothing in the body can name
    somebody else. A report that cannot be kept is lost quietly: the page that
    sends it does not wait for the answer and shows nothing either way.
    """
    facts = request_facts.get() or facts_of(request)
    if not await redis_client.check_rate_limit(
        f"activity:device:{facts.device_key}", REPORTS_PER_DEVICE_PER_MINUTE, 60
    ) or not await redis_client.check_rate_limit(
        f"activity:ip:{facts.ip}", REPORTS_PER_ADDRESS_PER_MINUTE, 60
    ):
        raise RateLimitError()
    try:
        accepted = await service.record(
            batch,
            facts,
            parse_optional_int(request.headers.get("x-actor-id")),
            parse_optional_int(request.headers.get("x-company-id")),
        )
    except Exception:
        logger.exception("Activity report failed")
        accepted = 0
    return ActivityAccepted(accepted=accepted)
