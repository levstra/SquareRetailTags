import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from esl.catalog.sync import sync_business
from esl.db import business_session
from esl.square import connections
from esl.square.webhooks import WebhookNotConfigured, is_valid_signature

logger = logging.getLogger(__name__)
router = APIRouter()

CATALOG_UPDATED = "catalog.version.updated"
OAUTH_REVOKED = "oauth.authorization.revoked"


async def handle_event(event_type: str, merchant_id: str | None) -> None:
    business_id = await connections.business_for_merchant(merchant_id)
    if business_id is None:
        logger.info("Square webhook %s for unknown merchant %s ignored", event_type, merchant_id)
        return
    if event_type == OAUTH_REVOKED:
        async with business_session(business_id) as session:
            await connections.set_status(session, business_id, "revoked")
            await session.commit()
        logger.warning("Square access revoked for business %s", business_id)
        return
    await sync_business(business_id)


@router.post("/webhooks/square", status_code=200)
async def square_webhook(request: Request, background_tasks: BackgroundTasks):
    body = (await request.body()).decode()
    try:
        valid = is_valid_signature(body, request.headers.get("x-square-hmacsha256-signature", ""))
    except WebhookNotConfigured:
        raise HTTPException(status_code=503, detail="Webhook not configured")
    if not valid:
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        event = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="Invalid event")

    event_type = event.get("type", "")
    logger.info("Square webhook: %s (event_id=%s)", event_type, event.get("event_id"))
    if event_type in (CATALOG_UPDATED, OAUTH_REVOKED):
        # Unknown merchants still get 200 so Square doesn't retry forever.
        background_tasks.add_task(handle_event, event_type, event.get("merchant_id"))
    return {"ok": True}
