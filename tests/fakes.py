import json
from pathlib import Path

from square.types.list_locations_response import ListLocationsResponse
from square.types.obtain_token_response import ObtainTokenResponse
from square.types.search_catalog_objects_response import SearchCatalogObjectsResponse

from esl.accounts.identity import EmailAlreadyRegistered

FIXTURES = Path(__file__).parent / "fixtures" / "square"


class FakeIdentityProvider:
    """In-memory stand-in for Cognito admin calls."""

    def __init__(self):
        self.users: dict[str, dict] = {}

    def invite(self, email: str) -> str:
        if email in self.users:
            raise EmailAlreadyRegistered(email)
        sub = f"sub-{email}"
        self.users[email] = {"sub": sub, "enabled": True}
        return sub

    def disable(self, email: str) -> None:
        self.users[email]["enabled"] = False

    def enable(self, email: str) -> None:
        self.users[email]["enabled"] = True

    def delete(self, email: str) -> None:
        self.users.pop(email, None)


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def square_override(location_id: str, price: int | None = None, pricing_type: str | None = None) -> dict:
    override: dict = {"location_id": location_id}
    if price is not None:
        override["price_money"] = {"amount": price, "currency": "USD"}
    if pricing_type is not None:
        override["pricing_type"] = pricing_type
    return override


def square_variation(var_id: str, name: str, price: int | None, *, sku: str | None = None,
                     pricing_type: str = "FIXED_PRICING", overrides: tuple = (),
                     present_at_all_locations: bool = True,
                     present_at_location_ids: list[str] | None = None,
                     absent_at_location_ids: list[str] | None = None) -> dict:
    data: dict = {"name": name, "pricing_type": pricing_type, "location_overrides": list(overrides)}
    if sku is not None:
        data["sku"] = sku
    if price is not None:
        data["price_money"] = {"amount": price, "currency": "USD"}
    variation: dict = {"type": "ITEM_VARIATION", "id": var_id, "updated_at": "2026-10-04T11:00:00.000Z",
                       "present_at_all_locations": present_at_all_locations, "item_variation_data": data}
    if present_at_location_ids is not None:
        variation["present_at_location_ids"] = present_at_location_ids
    if absent_at_location_ids is not None:
        variation["absent_at_location_ids"] = absent_at_location_ids
    return variation


def square_item(item_id: str, name: str, variations: list[dict]) -> dict:
    return {"type": "ITEM", "id": item_id, "updated_at": "2026-10-04T11:00:00.000Z",
            "item_data": {"name": name, "variations": variations}}


def token_response(merchant_id: str = "MERCHANT_A", *, access_token: str = "at-new",
                   refresh_token: str | None = "rt-new",
                   expires_at: str = "2026-11-03T12:00:00Z") -> ObtainTokenResponse:
    body = {"access_token": access_token, "token_type": "bearer",
            "expires_at": expires_at, "merchant_id": merchant_id}
    if refresh_token is not None:
        body["refresh_token"] = refresh_token
    return ObtainTokenResponse.model_validate(body)


class FakeCatalog:
    def __init__(self):
        self.objects: list[dict] = []
        self.latest_time = "2026-10-04T12:00:00.000Z"
        self.calls: list[dict] = []
        self.error: Exception | None = None

    def search(self, **kwargs) -> SearchCatalogObjectsResponse:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SearchCatalogObjectsResponse.model_validate(
            {"objects": self.objects, "latest_time": self.latest_time}
        )


class FakeOAuth:
    def __init__(self):
        self.response = ObtainTokenResponse.model_validate(load_fixture("obtain_token.json"))
        self.calls: list[dict] = []
        self.error: Exception | None = None

    def obtain_token(self, **kwargs) -> ObtainTokenResponse:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeLocations:
    def __init__(self):
        self.response = ListLocationsResponse.model_validate(load_fixture("list_locations.json"))

    def list(self) -> ListLocationsResponse:
        return self.response


class FakeSquare:
    """Stands in for a `square.Square` client. Responses are real SDK models."""

    def __init__(self):
        self.catalog = FakeCatalog()
        self.o_auth = FakeOAuth()
        self.locations = FakeLocations()
