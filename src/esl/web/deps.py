import uuid
from collections.abc import AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from esl.config import settings
from esl.db import business_session


def get_request_business_id() -> uuid.UUID:
    """The business this request acts for.

    TEMPORARY single-business mode (rollout step 1). Step 2 derives this from
    the Cognito user, step 4 from the gateway key. Never read it from the
    request path, query, or body.
    """
    return uuid.UUID(settings.default_business_id)


async def get_business_session(
    business_id: uuid.UUID = Depends(get_request_business_id),
) -> AsyncIterator[AsyncSession]:
    async with business_session(business_id) as session:
        yield session
