import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from esl.auth.current_user import CurrentUser
from esl.businesses.models import Store
from esl.gateway.models import AccessPointStatus, Gateway, LabelTelemetry
from esl.tags.models import Tag
from esl.web.deps import get_business_session, get_current_user, require_owner
from esl.web.stores import get_store_for_user

router = APIRouter()


class StoreLabel(BaseModel):
    tag_id: str
    last_seen_at: datetime | None
    low_battery: bool
    battery: int | None
    assigned: bool
    variation_id: str | None


class StoreLabelList(BaseModel):
    labels: list[StoreLabel]


class GatewayOut(BaseModel):
    id: uuid.UUID
    name: str
    key_prefix: str
    last_seen_at: datetime | None
    revoked: bool


class AccessPointOut(BaseModel):
    ap_id: str
    connected: bool
    stale: bool
    last_heartbeat_at: datetime | None
    reported_at: datetime


class StoreGateways(BaseModel):
    gateways: list[GatewayOut]
    access_points: list[AccessPointOut]


@router.get("/stores/{store_id}/labels", response_model=StoreLabelList)
async def list_store_labels(
    store_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    await get_store_for_user(session, user, store_id)
    rows = (await session.execute(
        select(LabelTelemetry, Tag.variation_id)
        # Store too: a tag paired in another store of this business isn't assigned here.
        .outerjoin(Tag, and_(Tag.id == LabelTelemetry.tag_id, Tag.business_id == LabelTelemetry.business_id,
                             Tag.store_id == LabelTelemetry.store_id))
        .where(LabelTelemetry.store_id == store_id)
        .order_by(LabelTelemetry.tag_id)
    )).all()
    return StoreLabelList(labels=[
        StoreLabel(tag_id=t.tag_id, last_seen_at=t.last_seen_at, low_battery=t.low_battery,
                   battery=t.battery, assigned=variation_id is not None, variation_id=variation_id)
        for t, variation_id in rows
    ])


@router.get("/stores/{store_id}/gateways", response_model=StoreGateways)
async def list_store_gateways(
    store_id: uuid.UUID,
    owner: CurrentUser = Depends(require_owner),
    session: AsyncSession = Depends(get_business_session),
):
    await get_store_for_user(session, owner, store_id)
    gateways = (await session.execute(
        select(Gateway).where(Gateway.store_id == store_id).order_by(Gateway.created_at)
    )).scalars().all()
    aps = (await session.execute(
        select(AccessPointStatus).where(AccessPointStatus.store_id == store_id).order_by(AccessPointStatus.ap_id)
    )).scalars().all()
    return StoreGateways(
        gateways=[GatewayOut(id=g.id, name=g.name, key_prefix=g.key_prefix, last_seen_at=g.last_seen_at,
                             revoked=g.revoked_at is not None) for g in gateways],
        access_points=[AccessPointOut(ap_id=a.ap_id, connected=a.connected, stale=a.stale,
                                      last_heartbeat_at=a.last_heartbeat_at, reported_at=a.reported_at)
                       for a in aps],
    )
