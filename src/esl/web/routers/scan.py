import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from esl.auth.current_user import CurrentUser
from esl.scan import EmptyScan, classify, normalize_tag_id
from esl.web.deps import get_business_session, get_current_user
from esl.web.stores import get_store_for_user

router = APIRouter()

# A tag is "known" if this business registered it, or this store's Pi has heard it.
# RLS limits both tables to the user's business.
_KNOWN_TAG = text(
    "select exists (select 1 from tags where id = :tag_id) "
    "or exists (select 1 from label_telemetry where store_id = :store_id and tag_id = :tag_id)"
)


class ClassifyResult(BaseModel):
    kind: Literal["tag", "product"]
    normalized: str


@router.get("/scan/classify", response_model=ClassifyResult)
async def classify_scan(
    code: str,
    store_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    store = await get_store_for_user(session, user, store_id)
    tag_id = normalize_tag_id(code)
    known: set[str] = set()
    if tag_id is not None and await session.scalar(_KNOWN_TAG, {"tag_id": tag_id, "store_id": store.id}):
        known.add(tag_id)
    try:
        kind = classify(code, known_tag_ids=known)
    except EmptyScan:
        raise HTTPException(status_code=400, detail="Empty scan")
    return ClassifyResult(kind=kind, normalized=tag_id if kind == "tag" else code.strip())
