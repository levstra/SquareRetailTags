from esl.tags.projection import Projection


def format_amount(cents: int) -> str:
    """Integer cents → the contract's decimal string. Integer math only: 14999 → "149.99"."""
    if cents < 0:
        raise ValueError("price must not be negative")
    return f"{cents // 100}.{cents % 100:02d}"


def build_display(projection: Projection, currency: str) -> dict:
    """Semantic data aligned with Square (contract §2). The Pi decides the layout."""
    display: dict = {
        "itemName": projection["name"],
        "price": {"amount": format_amount(projection["price"]), "currency": currency},
    }
    if projection["variation_name"] is not None:
        display["variationName"] = projection["variation_name"]  # sent as-is, incl. "Regular"
    if projection["sku"]:
        display["sku"] = projection["sku"]
    return display
