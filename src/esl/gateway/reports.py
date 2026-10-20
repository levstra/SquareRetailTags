"""Status batches (contract sections 1.2, 3, 5). Validate everything, then apply."""
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from esl.gateway.auth import GatewayContext
from esl.gateway.commands import enqueue_retry
from esl.gateway.models import DISPLAY_ACTIONS, FINAL_STATUSES, LabelUpdate
from esl.gateway.wire import StatusReportIn
from esl.tags.desired import apply_desired_state
from esl.tags.models import Tag


class ForeignReport(ValueError):
    """A report for a command that doesn't exist or belongs to another store."""


async def apply_status_batch(session: AsyncSession, gateway: GatewayContext, reports: list[StatusReportIn]) -> int:
    ids = {r.update_id for r in reports}
    commands = {
        c.update_id: c
        for c in (await session.execute(
            select(LabelUpdate).where(LabelUpdate.update_id.in_(ids)).with_for_update()
        )).scalars()
    } if ids else {}
    for report in reports:
        command = commands.get(report.update_id)
        if command is None or command.store_id != gateway.store_id:
            raise ForeignReport(str(report.update_id))
    for report in reports:
        await _apply(session, commands[report.update_id], report)
    await session.commit()
    return len(reports)  # every valid entry counts, including duplicates and stale ones


async def _apply(session: AsyncSession, command: LabelUpdate, report: StatusReportIn) -> None:
    if command.status_at is not None and report.status_at <= command.status_at:
        return  # duplicate or out of order: the stored state is newer
    if report.status == "Accepted":
        if command.status != "Pending":
            return  # a terminal command never regresses to Dispatched
        command.status = "Dispatched"
        # Server clock: dispatched_at drives the 30-minute re-pend timer. The Pi's statusAt
        # for Accepted is its original QueuedAt, which after a re-pend is already >30 min old
        # and would re-pend the command again on every reconcile run.
        command.dispatched_at = func.now()
    else:
        command.status = report.status
        command.final_at = report.status_at
    command.status_at = report.status_at
    command.attempt = report.attempt
    command.ap_id = report.ap_id
    command.failure_code = report.failure_code
    command.failure_message = report.failure_message
    command.retryable = report.retryable
    command.diagnostics = report.diagnostics.model_dump(by_alias=True) if report.diagnostics else None

    if command.action not in DISPLAY_ACTIONS:
        return
    if report.status == "Succeeded":
        await _supersede_lower_versions(session, command, report)
        tag = await session.get(Tag, command.tag_id)
        if tag is not None and tag.version == command.version:
            tag.last_confirmed_at = report.status_at
    elif report.status == "Failed":  # enqueue_retry decides whether it's retried
        tag = await session.get(Tag, command.tag_id)
        if tag is not None:
            await enqueue_retry(session, tag, command)
    elif report.status == "Superseded" and report.known_version is not None:
        await _reissue_above_gateway_version(session, command, report.known_version)


async def _reissue_above_gateway_version(session: AsyncSession, command: LabelUpdate, known_version: int) -> None:
    """The Pi rejected central's latest version as not newer than one it already holds
    (contract section 5): central's ladder is behind the Pi's, e.g. after the central
    database restarted versions at 0. Send the current desired state once, above it.

    A rejection of an older version means central has already moved on, and the stored
    report short-circuits duplicates before we get here, so this sends at most once.
    """
    tag = await session.get(Tag, command.tag_id)
    if tag is None or tag.version != command.version or known_version < command.version:
        return
    await apply_desired_state(session, tag, resend_unconfirmed=True, above_version=known_version)


async def _supersede_lower_versions(session: AsyncSession, command: LabelUpdate, report: StatusReportIn) -> None:
    # Contract section 5: Succeeded for version N makes every lower version obsolete.
    await session.execute(
        update(LabelUpdate)
        .where(LabelUpdate.business_id == command.business_id, LabelUpdate.tag_id == command.tag_id,
               LabelUpdate.version < command.version, LabelUpdate.status.not_in(FINAL_STATUSES))
        .values(status="Superseded", final_at=report.status_at,
                failure_message=f"Version {command.version} succeeded")
    )
