from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from esl.gateway.models import DISPLAY_ACTIONS, LabelTelemetry, LabelUpdate
from esl.tags.models import Tag


@dataclass(frozen=True)
class TagExtras:
    latest_update: LabelUpdate | None
    telemetry: LabelTelemetry | None


async def load_tag_extras(session: AsyncSession, tags: list[Tag]) -> dict[str, TagExtras]:
    ids = [t.id for t in tags]
    if not ids:
        return {}
    latest = {
        c.tag_id: c
        for c in (await session.execute(
            select(LabelUpdate)
            .where(LabelUpdate.tag_id.in_(ids), LabelUpdate.action.in_(DISPLAY_ACTIONS))
            .order_by(LabelUpdate.tag_id, LabelUpdate.version.desc())
            .ext(distinct_on(LabelUpdate.tag_id))
        )).scalars()
    }
    telemetry = {
        (t.store_id, t.tag_id): t
        for t in (await session.execute(select(LabelTelemetry).where(LabelTelemetry.tag_id.in_(ids)))).scalars()
    }
    return {t.id: TagExtras(latest.get(t.id), telemetry.get((t.store_id, t.id))) for t in tags}
