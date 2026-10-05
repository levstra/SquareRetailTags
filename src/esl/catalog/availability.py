from esl.catalog.models import CatalogVariation


def is_available_at(variation: CatalogVariation, square_location_id: str | None) -> bool:
    """Whether Square sells this variation at the location (None = no location known)."""
    if square_location_id is None:
        return True
    if variation.present_at_all_locations:
        return square_location_id not in (variation.absent_at_location_ids or [])
    return square_location_id in (variation.present_at_location_ids or [])
