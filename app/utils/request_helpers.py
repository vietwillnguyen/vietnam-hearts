"""
Request utility helpers

Shared helpers for extracting common information from FastAPI requests.
"""

from fastapi import Request

from app import config
from app.utils.logging_config import get_logger

logger = get_logger("request_helpers")

FORWARDED_FOR_HEADER = "X-Forwarded-For"

# The clamp below reports a static misconfiguration, so it never self-resolves
# and every request would repeat it. PERSIST_LOGS_TO_DB defaults on, which turns
# each repeat into a system_logs row, so the warning is emitted once per process.
_clamp_warning_emitted = False

# Longest X-Forwarded-For value echoed into the per-request log line. The header
# is caller-extendable, so an unbounded echo would hand the caller control of the
# size of every system_logs row.
_MAX_LOGGED_FORWARDED_FOR = 200


class _BucketCollapseDetector:
    """
    Warn when ``TRUSTED_PROXY_HOPS`` looks too LOW for the deployment.

    Too high is already loud: ``get_client_ip`` clamps and warns on the spot,
    because the request itself carries the evidence. Too low is silent and the
    worse failure - every request resolves to the same shared infrastructure
    address, so every caller lands in one rate limit bucket and one attacker
    locks everyone out of ``/auth`` together.

    Nothing in a single request reveals it; the signature only appears across
    requests, as a resolved address that never varies. Detecting that needs no
    history, only the first address of a window and how many later samples
    matched it: constant memory, constant work per request, one warning per
    process rather than one log record per request.

    Only requests carrying more entries than we count in are sampled. When the
    counts are equal, nothing sits to the left of the resolved entry, so a
    higher hop count could not have picked a different address and a constant
    result proves nothing.

    A single client that genuinely dominates traffic *and* sends its own
    X-Forwarded-For prefix trips this too, which is why the message asks the
    operator to confirm against Cloud Run's request log instead of asserting a
    misconfiguration.

    The counters are advisory and deliberately unlocked: they are fed from
    async middleware on the event loop, and a lost increment only shifts when a
    window closes.
    """

    # Enough requests that ordinary low traffic from one client cannot trip it.
    WINDOW = 100
    # Below 1.0 so a stray sample - a scanner, a probe - cannot mask a genuine
    # collapse.
    RATIO = 0.9

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._candidate: str | None = None
        self._entry_count = 0
        self._matches = 0
        self._samples = 0
        self._warned = False

    def observe(self, resolved_ip: str, entry_count: int, hops: int) -> None:
        """Record one resolution; warn if the window shows a collapse."""
        if self._warned or entry_count <= hops:
            return

        if self._samples == 0:
            self._candidate = resolved_ip
            self._entry_count = entry_count

        self._samples += 1
        if resolved_ip == self._candidate:
            self._matches += 1

        if self._samples < self.WINDOW:
            return

        if self._matches >= self.RATIO * self._samples:
            self._warned = True
            logger.warning(
                "X-Forwarded-For bucket collapse suspected: %d of the last %d "
                "requests carrying more entries than TRUSTED_PROXY_HOPS=%d all "
                "resolved to client_ip=%s (%d entries in the header), so every "
                "caller is sharing one rate limit bucket. Settle it against the "
                "Cloud Run request log (logName run.googleapis.com/requests): if "
                "httpRequest.remoteIp for the same request differs from %s, "
                "TRUSTED_PROXY_HOPS is too low. Expected if one client "
                "legitimately sends nearly all of your traffic.",
                self._matches,
                self._samples,
                hops,
                self._candidate,
                self._entry_count,
                self._candidate,
            )
            return

        # Addresses varied, so the hop count is doing its job. Start a fresh
        # window rather than stopping, so a later topology change is still seen.
        self.reset()


_collapse_detector = _BucketCollapseDetector()


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

    forwarded_for = request.headers.get(FORWARDED_FOR_HEADER)
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
            resolved = entries[len(entries) - hops]
            _collapse_detector.observe(resolved, len(entries), hops)
            return resolved

    # X-Real-IP is deliberately not consulted: it is caller-supplied, Cloud Run
    # does not set it, and honouring it would reopen the same bypass.
    return request.client.host if request.client else "unknown"


def format_forwarded_for(request: Request) -> str:
    """
    Render the raw X-Forwarded-For for the per-request log line.

    Pairing this with the resolved client IP in one log record is what lets an
    operator settle ``TRUSTED_PROXY_HOPS`` empirically: take one request, read
    the chain as received and the address we picked out of it, then compare
    against ``httpRequest.remoteIp`` in the same request's Cloud Run request
    log. A logging ``extra`` field cannot carry either value - CloudRunJSONFormatter
    emits only severity/message/logger/time and DatabaseLogHandler persists only
    the formatted message - so both belong in the message text itself.

    Quoted because the value contains commas and spaces of its own. Truncated
    from the left when oversized, since the trustworthy end is the right-hand
    one that ``get_client_ip`` counts in from.
    """
    raw = request.headers.get(FORWARDED_FOR_HEADER)
    if raw is None:
        return "-"
    if len(raw) > _MAX_LOGGED_FORWARDED_FOR:
        return f'"...{raw[-_MAX_LOGGED_FORWARDED_FOR:]}"'
    return f'"{raw}"'
