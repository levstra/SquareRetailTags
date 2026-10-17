from typing import TypedDict

from esl.catalog.models import CatalogItem, CatalogVariation, VariationLocationPrice


class Projection(TypedDict):
    name: str | None
    variation_name: str | None
    sku: str | None
    price: int | None  # integer cents; None when VARIABLE_PRICING
    pricing_type: str | None


def build_projection(
    item: CatalogItem,
    variation: CatalogVariation,
    override: VariationLocationPrice | None = None,
) -> Projection:
    # A location override replaces price and pricing type only. The field set
    # never changes, so tags without an override keep byte-identical hashes.
    price, pricing_type = variation.price, variation.pricing_type
    if override is not None:
        price, pricing_type = override.price, override.pricing_type
    return Projection(
        name=item.name,
        variation_name=variation.variation_name,
        sku=variation.sku,
        price=price,
        pricing_type=pricing_type,
    )


UNASSIGNED: Projection = Projection(
    name=None,
    variation_name=None,
    sku=None,
    price=None,
    pricing_type=None,
)
