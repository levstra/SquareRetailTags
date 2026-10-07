import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from esl.auth.current_user import CurrentUser
from esl.catalog.availability import available_at_clause, is_available_at
from esl.catalog.models import CatalogItem, CatalogVariation
from esl.catalog.pricing import store_overrides
from esl.scan import gtin_candidates
from esl.tags.projection import build_projection
from esl.web.deps import get_business_session, get_current_user
from esl.web.stores import get_store_for_user

router = APIRouter()

SEARCH_LIMIT = 50


class VariationResponse(BaseModel):
    id: str
    item_id: str
    name: str
    variation_name: str
    sku: str | None = None
    gtin: str | None = None
    price: int | None = None
    pricing_type: str
    currency: str | None = None


class VariationListResponse(BaseModel):
    variations: list[VariationResponse]


class LookupResult(BaseModel):
    matches: list[VariationResponse]


def _like(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _priced(variation: CatalogVariation, override) -> VariationResponse:
    projection = build_projection(variation.item, variation, override)
    return VariationResponse(
        id=variation.id,
        item_id=variation.item_id,
        name=projection["name"],
        variation_name=projection["variation_name"],
        sku=variation.sku,
        gtin=variation.gtin,
        price=projection["price"],
        pricing_type=projection["pricing_type"],
        currency=variation.currency,
    )


@router.get("/catalog/variations", response_model=VariationListResponse)
async def list_variations(
    q: str | None = None,
    store_id: uuid.UUID | None = None,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    store = await get_store_for_user(session, user, store_id) if store_id else None
    location = store.square_location_id if store else None
    query = select(CatalogVariation).options(selectinload(CatalogVariation.item))
    if location is not None:  # before the LIMIT, so a store's results aren't cut short
        query = query.where(available_at_clause(location))
    if q and q.strip():
        pattern = _like(q.strip())
        query = (
            query.join(CatalogItem, and_(
                CatalogItem.business_id == CatalogVariation.business_id,
                CatalogItem.id == CatalogVariation.item_id,
            ))
            .where(or_(
                CatalogItem.name.ilike(pattern, escape="\\"),
                CatalogVariation.variation_name.ilike(pattern, escape="\\"),
                CatalogVariation.sku.ilike(pattern, escape="\\"),
            ))
            .order_by(CatalogItem.name, CatalogVariation.variation_name)
            .limit(SEARCH_LIMIT)
        )
    rows = (await session.execute(query)).scalars().all()
    overrides = await store_overrides(session, [v.id for v in rows], location)
    return VariationListResponse(variations=[_priced(v, overrides.get(v.id)) for v in rows])


@router.get("/catalog/lookup", response_model=LookupResult)
async def lookup_product(
    code: str,
    store_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    store = await get_store_for_user(session, user, store_id)
    code = code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="Empty code")

    base = select(CatalogVariation).options(selectinload(CatalogVariation.item))
    rows: list[CatalogVariation] = []
    candidates = gtin_candidates(code)
    if candidates:  # GTIN first
        rows = list((await session.execute(base.where(CatalogVariation.gtin.in_(candidates)))).scalars())
    if not rows:  # then SKU (exact; Code 128 SKU labels encode the SKU verbatim)
        rows = list((await session.execute(base.where(CatalogVariation.sku == code))).scalars())

    rows = [v for v in rows if is_available_at(v, store.square_location_id)]
    rows.sort(key=lambda v: (v.item.name, v.variation_name, v.id))
    overrides = await store_overrides(session, [v.id for v in rows], store.square_location_id)
    return LookupResult(matches=[_priced(v, overrides.get(v.id)) for v in rows])
