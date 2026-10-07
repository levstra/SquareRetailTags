from sqlalchemy import ColumnElement, and_, any_, literal, not_, or_

from esl.catalog.models import CatalogVariation


def is_available_at(variation: CatalogVariation, square_location_id: str | None) -> bool:
    """Whether Square sells this variation at the location (None = no location known)."""
    if square_location_id is None:
        return True
    if variation.present_at_all_locations:
        return square_location_id not in (variation.absent_at_location_ids or [])
    return square_location_id in (variation.present_at_location_ids or [])


def available_at_clause(square_location_id: str) -> ColumnElement[bool]:
    """`is_available_at` as a SQL filter, so a LIMIT applies after availability."""
    return or_(
        and_(CatalogVariation.present_at_all_locations,
             not_(literal(square_location_id) == any_(CatalogVariation.absent_at_location_ids))),
        and_(not_(CatalogVariation.present_at_all_locations),
             literal(square_location_id) == any_(CatalogVariation.present_at_location_ids)),
    )
