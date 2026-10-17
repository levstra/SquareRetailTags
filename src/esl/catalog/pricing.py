from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from esl.catalog.models import VariationLocationPrice


async def store_overrides(
    session: AsyncSession, variation_ids: list[str], square_location_id: str | None
) -> dict[str, VariationLocationPrice]:
    """Per-location price overrides for these variations at one Square location."""
    if not square_location_id or not variation_ids:
        return {}
    rows = (await session.execute(
        select(VariationLocationPrice).where(
            VariationLocationPrice.variation_id.in_(variation_ids),
            VariationLocationPrice.square_location_id == square_location_id,
        )
    )).scalars()
    return {row.variation_id: row for row in rows}
