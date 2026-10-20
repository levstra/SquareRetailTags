import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from esl.auth.current_user import CurrentUser
from esl.gateway.commands import retry_delay
from esl.gateway.models import LabelUpdate
from esl.web.deps import get_business_session, get_current_user

router = APIRouter()


class LabelUpdateSummary(BaseModel):
    update_id: uuid.UUID
    action: str
    status: str
    failure_code: str | None = None
    retryable: bool | None = None
    status_at: datetime | None = None


def summarize(command: LabelUpdate) -> LabelUpdateSummary:
    # retryable here is this service's decision, not the Pi's flag: it overrides the Pi
    # for LabelHardwareError and stops after the last retry.
    retryable = retry_delay(command) is not None if command.status == "Failed" else None
    return LabelUpdateSummary(update_id=command.update_id, action=command.action, status=command.status,
                              failure_code=command.failure_code, retryable=retryable,
                              status_at=command.status_at)


@router.get("/label-updates/{update_id}", response_model=LabelUpdateSummary)
async def get_label_update(
    update_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    command = await session.get(LabelUpdate, update_id)
    if command is None:
        raise HTTPException(status_code=404, detail="Label update not found")
    if not user.can_use_store(command.store_id):
        raise HTTPException(status_code=403, detail="You don't have access to this store")
    return summarize(command)
