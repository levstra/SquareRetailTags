import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from esl.businesses.models import Store
from esl.catalog.models import CatalogVariation
from esl.tags.hashing import hash_projection
from esl.tags.models import Tag
from esl.tags.projection import UNASSIGNED, build_projection
from esl.web.deps import get_business_session, get_request_business_id

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


def _tag_to_response(tag: Tag) -> TagResponse:
    if tag.variation is not None:
        proj = build_projection(tag.variation.item, tag.variation)
    else:
        proj = UNASSIGNED

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


_TAG_OPTIONS = [
    selectinload(Tag.variation).selectinload(CatalogVariation.item)
]


@router.get("/tags", response_model=TagListResponse)
async def list_tags(session: AsyncSession = Depends(get_business_session)):
    tags = (
        await session.execute(select(Tag).options(*_TAG_OPTIONS))
    ).scalars().all()
    return TagListResponse(tags=[_tag_to_response(t) for t in tags])


@router.get("/tags/{tag_id}", response_model=TagResponse)
async def get_tag(tag_id: str, session: AsyncSession = Depends(get_business_session)):
    tag_id = _normalize_tag_id(tag_id)
    tag = await session.get(Tag, tag_id, options=_TAG_OPTIONS)
    if tag is None:
        raise HTTPException(status_code=404, detail=f"Tag {tag_id} not found")
    return _tag_to_response(tag)


async def _default_store_id(session: AsyncSession) -> uuid.UUID | None:
    # New tags go to the business's first store until pairing picks a store.
    return (
        await session.execute(select(Store.id).order_by(Store.created_at, Store.id).limit(1))
    ).scalar_one_or_none()


@router.put("/tags/{tag_id}", response_model=TagResponse)
async def assign_tag(
    tag_id: str,
    body: AssignBody,
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

    tag = await session.get(Tag, tag_id, options=_TAG_OPTIONS)
    if tag is None:
        store_id = await _default_store_id(session)
        if store_id is None:
            raise HTTPException(status_code=409, detail="Business has no stores")
        tag = Tag(id=tag_id, business_id=business_id, store_id=store_id)
        session.add(tag)

    tag.variation_id = body.variation_id

    if variation is not None:
        proj = build_projection(variation.item, variation)
        tag.content_hash = hash_projection(proj)
    else:
        tag.content_hash = hash_projection(UNASSIGNED)

    try:
        await session.commit()
    except IntegrityError:
        # RLS hid an existing tag with this ID in another business. Don't say so.
        await session.rollback()
        raise HTTPException(status_code=409, detail="This tag can't be used")

    tag = await session.get(Tag, tag_id, options=_TAG_OPTIONS, populate_existing=True)
    return _tag_to_response(tag)


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
