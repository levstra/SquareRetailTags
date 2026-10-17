"""The three endpoints the store Pi calls (WestgateLabels/docs/central-api.openapi.yaml)."""
import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from esl.gateway.auth import GatewayContext, authorize_store, get_gateway, get_gateway_session
from esl.gateway.models import LabelUpdate
from esl.gateway.reports import ForeignReport, apply_status_batch
from esl.gateway.telemetry import apply_telemetry
from esl.gateway.wire import StatusBatchIn, TelemetryIn, command_to_wire

router = APIRouter(tags=["gateway"])

LONG_POLL_STEP_SECONDS = 1.0


async def _json_object(request: Request) -> dict:
    try:
        body = json.loads(await request.body())
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    return body


@router.get("/api/label-updates/pending")
async def fetch_pending(
    store_id: str = Query(alias="storeId"),
    limit: int = Query(200, ge=1, le=1000),
    wait: int = Query(0, ge=0, le=60),
    gateway: GatewayContext = Depends(get_gateway),
    session: AsyncSession = Depends(get_gateway_session),
):
    await authorize_store(session, gateway, store_id)
    deadline = time.monotonic() + wait
    query = (
        select(LabelUpdate)
        .where(LabelUpdate.store_id == gateway.store_id, LabelUpdate.status == "Pending",
               or_(LabelUpdate.not_before.is_(None), LabelUpdate.not_before <= func.now()))
        .order_by(LabelUpdate.created_at, LabelUpdate.update_id)
        .limit(limit + 1)
    )
    while True:
        rows = (await session.execute(query)).scalars().all()
        if rows or time.monotonic() >= deadline:
            break
        await session.rollback()  # release the connection while holding the request
        await asyncio.sleep(LONG_POLL_STEP_SECONDS)
    return {
        "updates": [command_to_wire(c, gateway.store_code) for c in rows[:limit]],
        "hasMore": len(rows) > limit,
    }


@router.post("/api/label-updates/status")
async def report_status(
    request: Request,
    gateway: GatewayContext = Depends(get_gateway),
    session: AsyncSession = Depends(get_gateway_session),
):
    body = await _json_object(request)
    await authorize_store(session, gateway, body.get("storeId"))
    try:
        batch = StatusBatchIn.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"Malformed status batch ({exc.error_count()} error(s)); nothing applied")
    try:
        accepted = await apply_status_batch(session, gateway, batch.reports)
    except ForeignReport:
        raise HTTPException(
            status_code=400,
            detail="Every report must identify a command of this store; the whole batch was rejected",
        )
    return {"acceptedCount": accepted}


@router.post("/api/labels/telemetry")
async def report_telemetry(
    request: Request,
    gateway: GatewayContext = Depends(get_gateway),
    session: AsyncSession = Depends(get_gateway_session),
):
    body = await _json_object(request)
    await authorize_store(session, gateway, body.get("storeId"))
    try:
        report = TelemetryIn.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"Malformed telemetry ({exc.error_count()} error(s))")
    return {"acceptedCount": await apply_telemetry(session, gateway, report)}
