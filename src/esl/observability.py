import logging
import sys
import uuid

SYNC_FAILURE_MARKER = "square sync failed"  # CloudWatch metric filter matches this text

_sync_logger = logging.getLogger("esl.sync")


def log_sync_failure(business_id: uuid.UUID, exc: BaseException) -> None:
    _sync_logger.error(
        "%s business=%s error=%s", SYNC_FAILURE_MARKER, business_id, type(exc).__name__, exc_info=exc
    )


def configure_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
