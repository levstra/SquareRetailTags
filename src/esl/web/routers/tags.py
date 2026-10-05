import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from esl.auth.current_user import CurrentUser
from esl.businesses.models import Store
from esl.catalog.models import CatalogVariation
from esl.tags.desired import apply_desired_state, projections_for
from esl.tags.models import Tag
from esl.tags.projection import Projection
from esl.web.deps import get_business_session, get_current_user, get_request_business_id

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
    last_pushed_at: datetime | None = None
    last_confirmed_at: datetime | None = None
    battery_pct: int | None = None

    model_config = {"from_attributes": True}


class TagListResponse(BaseModel):
    tags: list[TagResponse]


class AssignBody(BaseModel):
    variation_id: str | None = None


class ConfirmBody(BaseModel):
    content_hash: str
    battery_pct: int | None = None

    @field_validator("battery_pct")
    @classmethod
    def battery_in_range(cls, v: int | None) -> int | None:
        if v is not None and not (0 <= v <= 100):
            raise ValueError("battery_pct must be 0–100")
        return v


def _normalize_tag_id(tag_id: str) -> str:
    return tag_id.upper()


def _tag_to_response(tag: Tag, proj: Projection) -> TagResponse:
    return TagResponse(
        id=tag.id,
        variation_id=tag.variation_id,
        content_hash=tag.content_hash,
        last_pushed_at=tag.last_pushed_at,
        last_confirmed_at=tag.last_confirmed_at,
        battery_pct=tag.battery_pct,
        name=proj["name"],
        variation_name=proj["variation_name"],
        sku=proj["sku"],
        price=proj["price"],
        pricing_type=proj["pricing_type"],
    )


def _ensure_store_allowed(user: CurrentUser, store_id: uuid.UUID) -> None:
    if not user.can_use_store(store_id):
        raise HTTPException(status_code=403, detail="You don't have access to this store")


@router.get("/tags", response_model=TagListResponse)
async def list_tags(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    query = select(Tag)
    if user.store_ids is not None:
        query = query.where(Tag.store_id.in_(user.store_ids))
    tags = (await session.execute(query)).scalars().all()
    projections = await projections_for(session, tags)
    return TagListResponse(tags=[_tag_to_response(t, projections[t.id]) for t in tags])


@router.get("/tags/{tag_id}", response_model=TagResponse)
async def get_tag(
    tag_id: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    tag_id = _normalize_tag_id(tag_id)
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail=f"Tag {tag_id} not found")
    _ensure_store_allowed(user, tag.store_id)
    projections = await projections_for(session, [tag])
    return _tag_to_response(tag, projections[tag.id])


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
    tag_id = _normalize_tag_id(tag_id)

    variation = None
    if body.variation_id is not None:
        variation = await session.get(
            CatalogVariation,
            {"business_id": business_id, "id": body.variation_id},
            options=[selectinload(CatalogVariation.item)],
        )
        if variation is None:
            raise HTTPException(status_code=404, detail=f"Variation {body.variation_id} not found")

    tag = await session.get(Tag, tag_id)
    if tag is None:
        store_id = await _default_store_id(session, user)
        if store_id is None:
            raise HTTPException(status_code=409, detail="Business has no stores")
        tag = Tag(id=tag_id, business_id=business_id, store_id=store_id)
        session.add(tag)
    else:
        _ensure_store_allowed(user, tag.store_id)

    tag.variation_id = body.variation_id

    try:
        # apply_desired_state queries (autoflush), so a foreign tag ID can
        # surface here as well as at commit.
        await apply_desired_state(session, tag)
        await session.commit()
    except IntegrityError:
        # RLS hid an existing tag with this ID in another business. Don't say so.
        await session.rollback()
        raise HTTPException(status_code=409, detail="This tag can't be used")

    tag = await session.get(Tag, tag_id, populate_existing=True)
    projections = await projections_for(session, [tag])
    return _tag_to_response(tag, projections[tag.id])


@router.post("/tags/{tag_id}/confirm", status_code=204)
async def confirm_tag(
    tag_id: str,
    body: ConfirmBody,
    session: AsyncSession = Depends(get_business_session),
):
    tag_id = _normalize_tag_id(tag_id)
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail=f"Tag {tag_id} not found")

    tag.last_confirmed_at = datetime.now(timezone.utc)
    if body.battery_pct is not None:
        tag.battery_pct = body.battery_pct

    await session.commit()
    return Response(status_code=204)
