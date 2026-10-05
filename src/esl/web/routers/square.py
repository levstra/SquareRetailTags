import asyncio
import logging
import secrets
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from esl.auth.current_user import CurrentUser
from esl.businesses.models import Business
from esl.catalog.models import SyncCursor
from esl.catalog.sync import sync_business
from esl.config import settings
from esl.db import AsyncSessionLocal, business_session
from esl.square import connections, oauth
from esl.square.models import OAuthState, SquareConnection
from esl.web.deps import get_business_session, get_current_user, require_owner

logger = logging.getLogger(__name__)
router = APIRouter()

# Binds the single-use state to the browser that started the connect, so a
# callback link can't be replayed from another browser. Scoped to the callback.
STATE_COOKIE = "esl_square_oauth_state"
CALLBACK_PATH = "/v1/square/oauth/callback"
# Matches the oauth_states expiry in esl_oauth_state_take (migration 0004).
STATE_TTL_SECONDS = 10 * 60


class SquareConnectionOut(BaseModel):
    status: Literal["not_connected", "active", "needs_reconnect", "revoked"]
    merchant_id: str | None = None


class AuthorizeUrl(BaseModel):
    authorize_url: str


def _cookie_secure() -> bool:
    return settings.public_base_url.startswith("https")


def _settings_redirect(result: str) -> RedirectResponse:
    base = settings.public_base_url.rstrip("/")
    response = RedirectResponse(f"{base}/settings/square?result={result}", status_code=302)
    response.delete_cookie(
        STATE_COOKIE, path=CALLBACK_PATH, secure=_cookie_secure(), httponly=True, samesite="lax"
    )
    return response


@router.get("/square/connection", response_model=SquareConnectionOut)
async def get_square_connection(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    connection = await session.get(SquareConnection, user.business_id)
    if connection is None:
        return SquareConnectionOut(status="not_connected")
    return SquareConnectionOut(status=connection.status, merchant_id=connection.merchant_id)


@router.post("/square/oauth/start", response_model=AuthorizeUrl)
async def start_square_oauth(
    response: Response,
    owner: CurrentUser = Depends(require_owner),
    session: AsyncSession = Depends(get_business_session),
):
    if not oauth.is_configured():
        raise HTTPException(status_code=503, detail="Square connection is not configured")
    state = secrets.token_urlsafe(32)
    session.add(OAuthState(state=state, business_id=owner.business_id, user_id=owner.user_id))
    await session.commit()
    response.set_cookie(
        STATE_COOKIE, state, max_age=STATE_TTL_SECONDS, path=CALLBACK_PATH,
        secure=_cookie_secure(), httponly=True, samesite="lax",
    )
    return AuthorizeUrl(authorize_url=oauth.authorize_url(state))


@router.get("/square/oauth/callback")
async def square_oauth_callback(
    background_tasks: BackgroundTasks,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    state_cookie: str | None = Cookie(None, alias=STATE_COOKIE),
):
    # Public route: the business comes only from the single-use state row,
    # and only when this browser is the one that started the connect.
    if not state or not state_cookie or not secrets.compare_digest(
        state.encode(), state_cookie.encode()
    ):
        return _settings_redirect("error")
    async with AsyncSessionLocal() as session:
        taken = (await session.execute(
            text("select business_id, user_id from esl_oauth_state_take(:s)"), {"s": state}
        )).one_or_none()
        await session.commit()
    if taken is None or error or not code:
        return _settings_redirect("error")
    business_id = taken.business_id

    try:
        grant = await asyncio.to_thread(oauth.exchange_code, code)
    except oauth.OAuthFailed:
        logger.warning("Square code exchange failed for business %s", business_id)
        return _settings_redirect("error")

    linked_to = await connections.business_for_merchant(grant.merchant_id)
    if linked_to is not None and linked_to != business_id:
        return _settings_redirect("merchant_in_use")

    async with business_session(business_id) as session:
        business = await session.get(Business, business_id)
        if business.square_merchant_id not in (None, grant.merchant_id):
            return _settings_redirect("different_merchant")
        first_link = business.square_merchant_id is None
        business.square_merchant_id = grant.merchant_id
        await connections.save_grant(session, business_id, grant)
        if first_link:
            # New catalog source: next sync starts from scratch.
            await session.execute(delete(SyncCursor).where(SyncCursor.business_id == business_id))
        try:
            await session.commit()
        except IntegrityError:
            # Another business linked this merchant concurrently.
            return _settings_redirect("merchant_in_use")

    # Every successful connect syncs (a reconnect may have missed webhooks).
    background_tasks.add_task(sync_business, business_id)
    return _settings_redirect("connected")
