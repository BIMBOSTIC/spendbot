import logging
import sentry_sdk
from config import SENTRY_DSN

logger = logging.getLogger(__name__)

# Keys that might carry raw user message text — never send to Sentry
_SCRUB_EXTRA = ("update", "message", "text", "raw", "raw_message", "body")


def _before_send(event: dict, hint: dict) -> dict | None:
    """Strip anything that could contain raw expense amounts or user message text."""
    # Logging breadcrumbs capture formatter args, which include user input
    for bc in (event.get("breadcrumbs") or {}).get("values", []):
        bc.pop("message", None)
        bc.pop("data", None)
    # Extra context attached explicitly — scrub known text-bearing keys
    extra = event.get("extra", {})
    for key in _SCRUB_EXTRA:
        extra.pop(key, None)
    return event


def init() -> None:
    if not SENTRY_DSN:
        logger.info("SENTRY_DSN not set — error tracking disabled")
        return
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=0.0,   # no performance tracing
        before_send=_before_send,
    )
    logger.info("Sentry initialised")


def capture(exc: BaseException, *, user_id: str | int | None = None, tag: str = "") -> None:
    """Report an exception with minimal safe context — no message text."""
    with sentry_sdk.push_scope() as scope:
        if user_id is not None:
            scope.set_user({"id": str(user_id)})
        if tag:
            scope.set_tag("job", tag)
        scope.set_tag("error_type", type(exc).__name__)
        sentry_sdk.capture_exception(exc)
