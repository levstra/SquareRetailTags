"""Square OAuth (code flow with app secret). Synchronous: call via asyncio.to_thread."""
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode

import httpx
from square import Square
from square.core.api_error import ApiError

from esl.config import settings
from esl.square.client import environment

SCOPES = ("ITEMS_READ", "MERCHANT_PROFILE_READ")


class OAuthFailed(Exception):
    """Square refused the code or refresh token, or answered without a token.

    `refused` is True only when Square itself rejected the request (a 4xx
    other than 408/429), i.e. the code or refresh token is dead. Transport
    errors, timeouts, rate limits, 5xx and malformed responses are transient.
    """

    def __init__(self, message: str, *, refused: bool = False):
        super().__init__(message)
        self.refused = refused


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


def _is_refusal(status_code: int | None) -> bool:
    return status_code is not None and 400 <= status_code < 500 and status_code not in (408, 429)


def _obtain(**kwargs) -> TokenGrant:
    try:
        response = oauth_client().o_auth.obtain_token(
            client_id=settings.square_application_id,
            client_secret=settings.square_application_secret,
            **kwargs,
        )
    except ApiError as exc:
        raise OAuthFailed(f"Square OAuth error {exc.status_code}", refused=_is_refusal(exc.status_code)) from exc
    except httpx.HTTPError as exc:
        # The SDK wraps only HTTP-status failures in ApiError; transport errors escape raw.
        raise OAuthFailed(f"Square OAuth unreachable: {type(exc).__name__}") from exc
    if response.errors or not (response.access_token and response.merchant_id and response.expires_at):
        raise OAuthFailed("Square OAuth response had no usable token")
    try:
        expires_at = datetime.fromisoformat(response.expires_at.replace("Z", "+00:00"))
    except ValueError as exc:
        # Message names the field only, never token values.
        raise OAuthFailed("Square OAuth response had an unreadable expires_at") from exc
    return TokenGrant(
        access_token=response.access_token,
        refresh_token=response.refresh_token,
        expires_at=expires_at,
        merchant_id=response.merchant_id,
    )
