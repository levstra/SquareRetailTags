import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from esl.auth.current_user import CurrentUser
from esl.businesses.models import Store
from esl.catalog.models import CatalogVariation
from esl.gateway.commands import enqueue_locate
from esl.scan import normalize_tag_id as scan_normalize_tag_id
from esl.tags.desired import apply_desired_state, desired_projection, projections_for
from esl.tags.models import Tag
from esl.tags.projection import Projection
from esl.tags.status import TagExtras, load_tag_extras
from esl.web.deps import get_business_session, get_current_user, get_request_business_id
from esl.web.routers.label_updates import LabelUpdateSummary, summarize
from esl.web.stores import get_store_for_user

router = APIRouter()


class TagResponse(BaseModel):
    id: str
    variation_id: str | None = None
    name: str | None = None
    variation_name: str | None = None
    sku: str | None = None
    price: int | None = None
    pricing_type: str | None = None
    content_hash: str | None = None
    last_confirmed_at: datetime | None = None
    version: int = 0
    store_id: uuid.UUID | None = None
    latest_update: LabelUpdateSummary | None = None
    low_battery: bool | None = None
    last_seen_at: datetime | None = None

    model_config = {"from_attributes": True}


class TagListResponse(BaseModel):
    tags: list[TagResponse]


class AssignBody(BaseModel):
    variation_id: str | None = None
    store_id: uuid.UUID | None = None


class LocateBody(BaseModel):
    seconds: int = Field(default=30, ge=1, le=300)


def normalize_tag_id(tag_id: str) -> str:
    """Tag IDs are 12 uppercase hex characters (the Pi's and the hardware's format)."""
    normalized = scan_normalize_tag_id(tag_id)
    if normalized is None:
        raise HTTPException(status_code=422, detail="Tag ID must be 12 hexadecimal characters")
    return normalized


def _tag_to_response(tag: Tag, proj: Projection) -> TagResponse:
    return TagResponse(
        id=tag.id,
        variation_id=tag.variation_id,
        content_hash=tag.content_hash,
        last_confirmed_at=tag.last_confirmed_at,
        version=tag.version,
        store_id=tag.store_id,
        name=proj["name"],
        variation_name=proj["variation_name"],
        sku=proj["sku"],
        price=proj["price"],
        pricing_type=proj["pricing_type"],
    )


def _with_extras(response: TagResponse, extras: TagExtras | None) -> TagResponse:
    if extras is None:
        return response
    return response.model_copy(update={
        "latest_update": summarize(extras.latest_update) if extras.latest_update else None,
        "low_battery": extras.telemetry.low_battery if extras.telemetry else None,
        "last_seen_at": extras.telemetry.last_seen_at if extras.telemetry else None,
    })


def _ensure_store_allowed(user: CurrentUser, store_id: uuid.UUID) -> None:
    if not user.can_use_store(store_id):
        raise HTTPException(status_code=403, detail="You don't have access to this store")


def _ensure_has_a_store(user: CurrentUser) -> None:
    if not user.has_store_access:
        raise HTTPException(status_code=403, detail="You don't have access to any store")


@router.get("/tags", response_model=TagListResponse)
async def list_tags(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    if not user.has_store_access:
        return TagListResponse(tags=[])
    query = select(Tag)
    if user.store_ids is not None:
        query = query.where(Tag.store_id.in_(user.store_ids))
    tags = (await session.execute(query)).scalars().all()
    projections = await projections_for(session, tags)
    extras = await load_tag_extras(session, tags)
    return TagListResponse(
        tags=[_with_extras(_tag_to_response(t, projections[t.id]), extras.get(t.id)) for t in tags]
    )


@router.get("/tags/{tag_id}", response_model=TagResponse)
async def get_tag(
    tag_id: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    tag_id = normalize_tag_id(tag_id)
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail=f"Tag {tag_id} not found")
    _ensure_store_allowed(user, tag.store_id)
    projections = await projections_for(session, [tag])
    extras = (await load_tag_extras(session, [tag])).get(tag.id)
    return _with_extras(_tag_to_response(tag, projections[tag.id]), extras)


async def _default_store_id(session: AsyncSession, user: CurrentUser) -> uuid.UUID | None:
    # New tags go to the first store this user may use, until pairing picks a store.
    query = select(Store.id).order_by(Store.created_at, Store.id).limit(1)
    if user.store_ids is not None:
        query = query.where(Store.id.in_(user.store_ids))
    return (await session.execute(query)).scalar_one_or_none()


@router.put("/tags/{tag_id}", response_model=TagResponse)
async def assign_tag(
    tag_id: str,
    body: AssignBody,
    user: CurrentUser = Depends(get_current_user),
    business_id: uuid.UUID = Depends(get_request_business_id),
    session: AsyncSession = Depends(get_business_session),
):
    tag_id = normalize_tag_id(tag_id)
    _ensure_has_a_store(user)

    variation = None
    if body.variation_id is not None:
        variation = await session.get(
            CatalogVariation,
            {"business_id": business_id, "id": body.variation_id},
            options=[selectinload(CatalogVariation.item)],
        )
        if variation is None:
            raise HTTPException(status_code=404, detail=f"Variation {body.variation_id} not found")

    # The store the tag was scanned in: 404 if invisible (another business), 403 if not allowed.
    target_store_id = None
    if body.store_id is not None:
        target_store_id = (await get_store_for_user(session, user, body.store_id)).id

    tag = await session.get(Tag, tag_id)
    if tag is None:
        if target_store_id is None:
            target_store_id = await _default_store_id(session, user)
            if target_store_id is None:
                raise HTTPException(status_code=409, detail="Business has no stores")
        tag = Tag(id=tag_id, business_id=business_id, store_id=target_store_id)
        session.add(tag)
    else:
        _ensure_store_allowed(user, tag.store_id)
        if target_store_id is not None:
            tag.store_id = target_store_id  # scanned in this store: it lives here now

    tag.variation_id = body.variation_id

    try:
        # apply_desired_state queries (autoflush), so a foreign tag ID can
        # surface here as well as at commit. The variable-price check queries
        # too, so it must sit inside this guard to keep the 409 for foreign tags.
        if body.variation_id is not None:
            # The contract needs a price; a VARIABLE_PRICING item (at this store) can't be shown.
            if (await desired_projection(session, tag))["price"] is None:
                await session.rollback()
                raise HTTPException(status_code=409, detail="Variable-priced items can't be shown on a label")
        await apply_desired_state(session, tag)
        await session.commit()
    except IntegrityError:
        # RLS hid an existing tag with this ID in another business. Don't say so.
        await session.rollback()
        raise HTTPException(status_code=409, detail="This tag can't be used")

    tag = await session.get(Tag, tag_id, populate_existing=True)
    projections = await projections_for(session, [tag])
    extras = (await load_tag_extras(session, [tag])).get(tag.id)
    return _with_extras(_tag_to_response(tag, projections[tag.id]), extras)


@router.post("/tags/{tag_id}/locate", response_model=LabelUpdateSummary)
async def locate_tag(
    tag_id: str,
    body: LocateBody | None = None,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    tag_id = normalize_tag_id(tag_id)
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail=f"Tag {tag_id} not found")
    _ensure_store_allowed(user, tag.store_id)
    update = await enqueue_locate(session, tag, seconds=(body or LocateBody()).seconds)
    await session.commit()
    return summarize(update)
