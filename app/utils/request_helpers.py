"""
Request utility helpers

Shared helpers for extracting common information from FastAPI requests.
"""

from fastapi import Request

from app import config
from app.utils.logging_config import get_logger

logger = get_logger("request_helpers")

# The clamp below reports a static misconfiguration, so it never self-resolves
# and every request would repeat it. PERSIST_LOGS_TO_DB defaults on, which turns
# each repeat into a system_logs row, so the warning is emitted once per process.
_clamp_warning_emitted = False


def get_client_ip(request: Request) -> str:
    """
    Get the client IP address, resisting caller-supplied spoofing.

    X-Forwarded-For is append-only and nothing along the path validates the
    entries already in it, so a caller can put whatever they like at the front.
    Reading the leftmost entry - the conventional but wrong choice - hands the
    caller control of their own rate limit bucket, which is what made the
    10-per-hour /auth limit escapable by rotating the header.

    The trustworthy end is the right: each proxy appends the peer address it
    actually observed. We therefore count ``config.TRUSTED_PROXY_HOPS`` entries
    in from the right, which is the number of entries our own infrastructure
    appends. Ignoring the header entirely would be just as wrong - on Cloud Run
    the direct peer is a shared internal front end, so every caller would land
    in one bucket and be locked out together.

    Falls back to the direct peer when no forwarded header is present, which is
    the local-development and test case.
    """
    global _clamp_warning_emitted

    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        entries = [entry.strip() for entry in forwarded_for.split(",")]
        entries = [entry for entry in entries if entry]
        if entries:
            hops = config.TRUSTED_PROXY_HOPS
            if hops > len(entries):
                # Fewer proxies in front of us than configured. Clamping to the
                # leftmost entry keeps callers in separate buckets; returning a
                # shared infrastructure address instead would collapse everyone
                # into one bucket and lock them out together.
                if not _clamp_warning_emitted:
                    _clamp_warning_emitted = True
                    logger.warning(
                        "X-Forwarded-For has %d entries but TRUSTED_PROXY_HOPS is %d; "
                        "clamping to the leftmost entry. Check the proxy topology.",
                        len(entries),
                        hops,
                    )
                return entries[0]
            return entries[len(entries) - hops]

    # X-Real-IP is deliberately not consulted: it is caller-supplied, Cloud Run
    # does not set it, and honouring it would reopen the same bypass.
    return request.client.host if request.client else "unknown"
