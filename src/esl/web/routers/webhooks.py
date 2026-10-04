import json
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from square.utils.webhooks_helper import verify_signature

from esl.catalog.sync import sync_catalog
from esl.config import settings
from esl.db import business_session

logger = logging.getLogger(__name__)
router = APIRouter()

CATALOG_UPDATED = "catalog.version.updated"


async def _run_sync() -> None:
    # TEMPORARY single-business mode. Step 3 resolves the business from the
    # event's merchant_id via businesses.square_merchant_id.
    business_id = uuid.UUID(settings.default_business_id)
    async with business_session(business_id) as session:
        result = await sync_catalog(session, business_id)
        logger.info("webhook sync complete for business %s: %s", business_id, result)


@router.post("/webhooks/square", status_code=200)
async def square_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    sig = request.headers.get("x-square-hmacsha256-signature", "")

    if not settings.square_webhook_signature_key:
        raise HTTPException(status_code=503, detail="Webhook not configured")

    try:
        valid = verify_signature(
            request_body=body.decode(),
            signature_header=sig,
            signature_key=settings.square_webhook_signature_key,
            notification_url=settings.square_webhook_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    if not valid:
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        event = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = event.get("type", "")
    logger.info("Square webhook: %s (event_id=%s)", event_type, event.get("event_id"))

    if event_type == CATALOG_UPDATED:
        background_tasks.add_task(_run_sync)

    return {"ok": True}
