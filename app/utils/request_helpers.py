"""
Request utility helpers

Shared helpers for extracting common information from FastAPI requests.
"""

from fastapi import Request


def get_client_ip(request: Request) -> str:
    """
    Get the client IP address from request headers.

    Respects X-Forwarded-For and X-Real-IP headers common in proxy setups.
    Falls back to the direct client host if neither header is present.
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip

    return request.client.host if request.client else "unknown"
