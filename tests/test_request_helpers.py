"""
Tests for client IP extraction and the rate limiting that depends on it.

The security property under test: a caller must not be able to choose which
rate limit bucket they land in by supplying their own X-Forwarded-For header.
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app import config
from app.middleware.rate_limit_middleware import RateLimitMiddleware
from app.utils.request_helpers import get_client_ip


def _request(
    headers: dict[str, str] | None = None, client_host: str | None = "10.0.0.1"
):
    """Build a minimal Starlette Request with the given headers."""
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": raw_headers,
        "client": (client_host, 12345) if client_host else None,
    }
    return Request(scope)


class TestGetClientIp:
    """Client IP extraction from the X-Forwarded-For header."""

    def test_single_entry_is_the_client(self):
        """Cloud Run appends the real client to XFF; unspoofed there is one entry."""
        assert (
            get_client_ip(_request({"X-Forwarded-For": "203.0.113.9"})) == "203.0.113.9"
        )

    def test_spoofed_prefix_is_ignored(self):
        """
        A caller supplying their own XFF has it preserved on the left and the
        real client appended on the right. Only the right-hand entry is real.
        """
        request = _request({"X-Forwarded-For": "1.2.3.4, 203.0.113.9"})
        assert get_client_ip(request) == "203.0.113.9"

    def test_long_spoofed_chain_is_ignored(self):
        """A caller cannot bury the real IP by supplying many entries."""
        spoofed = ", ".join(f"1.2.3.{n}" for n in range(1, 20))
        request = _request({"X-Forwarded-For": f"{spoofed}, 203.0.113.9"})
        assert get_client_ip(request) == "203.0.113.9"

    def test_x_real_ip_is_not_trusted(self):
        """
        X-Real-IP is caller-supplied and Cloud Run never sets it, so it must not
        override the direct peer.
        """
        request = _request({"X-Real-IP": "1.2.3.4"}, client_host="10.0.0.1")
        assert get_client_ip(request) == "10.0.0.1"

    def test_falls_back_to_peer_without_forwarded_header(self):
        assert get_client_ip(_request({}, client_host="10.0.0.7")) == "10.0.0.7"

    def test_unknown_when_no_peer_and_no_header(self):
        assert get_client_ip(_request({}, client_host=None)) == "unknown"

    def test_blank_entries_are_skipped(self):
        request = _request({"X-Forwarded-For": "1.2.3.4, , 203.0.113.9 ,"})
        assert get_client_ip(request) == "203.0.113.9"

    def test_header_of_only_separators_falls_back_to_peer(self):
        request = _request({"X-Forwarded-For": " , , "}, client_host="10.0.0.7")
        assert get_client_ip(request) == "10.0.0.7"

    def test_ipv6_entry_is_preserved(self):
        request = _request({"X-Forwarded-For": "1.2.3.4, 2001:db8::1"})
        assert get_client_ip(request) == "2001:db8::1"


class TestTrustedProxyHops:
    """The number of trusted hops is deployment-specific and configurable."""

    def test_two_hops_reads_second_from_the_right(self, monkeypatch):
        """
        Behind a Google external Application Load Balancer the header is
        '<supplied>,<client-ip>,<load-balancer-ip>', so the client is second
        from the right.
        """
        monkeypatch.setattr(config, "TRUSTED_PROXY_HOPS", 2)
        request = _request({"X-Forwarded-For": "1.2.3.4, 203.0.113.9, 35.191.0.1"})
        assert get_client_ip(request) == "203.0.113.9"

    def test_more_hops_than_entries_clamps_to_leftmost(self, monkeypatch):
        """
        A shorter-than-configured header means fewer proxies than expected.
        Clamp to the leftmost entry rather than returning a shared
        infrastructure address, which would collapse every caller into one
        rate limit bucket.
        """
        monkeypatch.setattr(config, "TRUSTED_PROXY_HOPS", 3)
        request = _request({"X-Forwarded-For": "203.0.113.9"})
        assert get_client_ip(request) == "203.0.113.9"


class TestRateLimitBucketing:
    """End-to-end: the /auth limit must not be escapable by rotating XFF."""

    @pytest.fixture
    def client(self):
        app = FastAPI()
        app.add_middleware(RateLimitMiddleware)

        @app.get("/auth/login")
        def login():
            return {"ok": True}

        return TestClient(app)

    def test_rotating_forwarded_for_does_not_reset_the_bucket(self, client):
        """
        Ten /auth requests per hour is the limit. Rotating the left-hand XFF
        entry used to mint a fresh bucket each time; the real client is the
        appended right-hand entry, so all of these share one bucket.
        """
        remaining = []
        for n in range(3):
            response = client.get(
                "/auth/login",
                headers={"X-Forwarded-For": f"1.2.3.{n}, 203.0.113.9"},
            )
            remaining.append(int(response.headers["X-RateLimit-Remaining"]))

        assert remaining == [
            9,
            8,
            7,
        ], "rotating the spoofable prefix minted a fresh bucket per request"

    def test_rotating_forwarded_for_still_gets_limited(self, client):
        """The 11th request is refused however the caller rewrites the header."""
        for n in range(10):
            client.get(
                "/auth/login",
                headers={"X-Forwarded-For": f"9.9.9.{n}, 198.51.100.4"},
            )

        blocked = client.get(
            "/auth/login",
            headers={"X-Forwarded-For": "9.9.9.99, 198.51.100.4"},
        )
        assert blocked.status_code == 429

    def test_distinct_real_clients_get_distinct_buckets(self, client):
        """The limit must still be per-client, not global."""
        first = client.get("/auth/login", headers={"X-Forwarded-For": "203.0.113.21"})
        second = client.get("/auth/login", headers={"X-Forwarded-For": "203.0.113.22"})

        assert int(first.headers["X-RateLimit-Remaining"]) == 9
        assert int(second.headers["X-RateLimit-Remaining"]) == 9
