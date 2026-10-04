import asyncio
import uuid
from collections.abc import AsyncIterator
from functools import lru_cache

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from esl.accounts.identity import IdentityError, IdentityProvider, make_identity_provider
from esl.auth.current_user import CurrentUser, load_current_user
from esl.auth.tokens import InvalidToken, TokenVerifier, cognito_verifier
from esl.config import settings
from esl.db import AsyncSessionLocal, business_session

_bearer = HTTPBearer(auto_error=False)
_UNAUTHORIZED = {"WWW-Authenticate": "Bearer"}


@lru_cache(maxsize=1)
def _cognito_verifier(region: str, pool_id: str, client_id: str) -> TokenVerifier:
    return cognito_verifier(region, pool_id, client_id)


def get_token_verifier() -> TokenVerifier:
    if not (settings.cognito_region and settings.cognito_user_pool_id and settings.cognito_app_client_id):
        raise HTTPException(status_code=503, detail="Sign-in is not configured")
    return _cognito_verifier(
        settings.cognito_region, settings.cognito_user_pool_id, settings.cognito_app_client_id
    )


@lru_cache(maxsize=1)
def _identity_provider() -> IdentityProvider:
    return make_identity_provider()


def get_identity_provider() -> IdentityProvider:
    try:
        return _identity_provider()
    except IdentityError:
        raise HTTPException(status_code=503, detail="Account management is not configured")


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    verifier: TokenVerifier = Depends(get_token_verifier),
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Sign in required", headers=_UNAUTHORIZED)
    try:
        # verify() may fetch Cognito's JWKS on first use; keep it off the event loop.
        sub = await asyncio.to_thread(verifier.verify, credentials.credentials)
    except InvalidToken:
        raise HTTPException(status_code=401, detail="Invalid or expired token", headers=_UNAUTHORIZED)

    # Unscoped session on purpose: it only calls esl_auth_user(), the one
    # function allowed to find a user before their business is known.
    async with AsyncSessionLocal() as session:
        user = await load_current_user(session, sub)
    if user is None:
        raise HTTPException(status_code=403, detail="This account has no access")
    return user


def get_request_business_id(user: CurrentUser = Depends(get_current_user)) -> uuid.UUID:
    """The business this request acts for: the signed-in user's. Never from the request."""
    return user.business_id


def require_owner(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_owner:
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


async def get_business_session(
    business_id: uuid.UUID = Depends(get_request_business_id),
) -> AsyncIterator[AsyncSession]:
    async with business_session(business_id) as session:
        yield session
