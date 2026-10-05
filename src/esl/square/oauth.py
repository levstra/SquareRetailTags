"""Square OAuth (code flow with app secret). Synchronous: call via asyncio.to_thread."""
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode

from square import Square
from square.core.api_error import ApiError

from esl.config import settings
from esl.square.client import environment

SCOPES = ("ITEMS_READ", "MERCHANT_PROFILE_READ")


class OAuthFailed(Exception):
    """Square refused the code or refresh token, or answered without a token."""


@dataclass(frozen=True)
class TokenGrant:
    access_token: str
    refresh_token: str | None  # refresh responses may omit it; keep the old one then
    expires_at: datetime
    merchant_id: str


def is_configured() -> bool:
    return bool(settings.square_application_id and settings.square_application_secret)


def redirect_uri() -> str:
    return f"{settings.public_base_url.rstrip('/')}/v1/square/oauth/callback"


def authorize_url(state: str) -> str:
    params = {
        "client_id": settings.square_application_id,
        "scope": " ".join(SCOPES),
        # Always show Square's login/approval screen (required in production).
        "session": "false",
        "state": state,
        "redirect_uri": redirect_uri(),
    }
    return f"{environment().value}/oauth2/authorize?{urlencode(params)}"


def oauth_client() -> Square:
    # ObtainToken authenticates with the app secret in the body, not a bearer token.
    return Square(environment=environment(), token=None, version=settings.square_api_version)


def exchange_code(code: str) -> TokenGrant:
    grant = _obtain(grant_type="authorization_code", code=code, redirect_uri=redirect_uri())
    if not grant.refresh_token:
        raise OAuthFailed("Square returned no refresh token")
    return grant


def refresh(refresh_token: str) -> TokenGrant:
    return _obtain(grant_type="refresh_token", refresh_token=refresh_token)


def _obtain(**kwargs) -> TokenGrant:
    try:
        response = oauth_client().o_auth.obtain_token(
            client_id=settings.square_application_id,
            client_secret=settings.square_application_secret,
            **kwargs,
        )
    except ApiError as exc:
        raise OAuthFailed(f"Square OAuth error {exc.status_code}") from exc
    if response.errors or not (response.access_token and response.merchant_id and response.expires_at):
        raise OAuthFailed("Square OAuth response had no usable token")
    return TokenGrant(
        access_token=response.access_token,
        refresh_token=response.refresh_token,
        expires_at=datetime.fromisoformat(response.expires_at.replace("Z", "+00:00")),
        merchant_id=response.merchant_id,
    )
