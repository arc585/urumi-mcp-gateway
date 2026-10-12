"""Unit tests for the small pure pieces: the token bucket and the SSRF URL check."""

import pytest
from app.ratelimit import RateLimiter
from app.upstream_url import validate_upstream_url


def test_token_bucket_allows_a_burst_then_refills(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("app.ratelimit.time.monotonic", lambda: clock[0])
    limiter = RateLimiter(per_minute=6)  # refills 0.1 tokens per second
    assert all(limiter.allow("u")[0] for _ in range(6))
    refused, wait = limiter.allow("u")
    assert not refused and 9.9 < wait <= 10.0  # one token takes 10 s to come back
    clock[0] += 10
    assert limiter.allow("u")[0]
    assert limiter.allow("other")[0]  # buckets are per key
    assert RateLimiter(per_minute=0).allow("u") == (True, 0.0)  # 0 disables limiting


def test_full_buckets_are_forgotten(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("app.ratelimit.time.monotonic", lambda: clock[0])
    limiter = RateLimiter(per_minute=60)
    limiter.allow("gone")
    clock[0] += 61  # bucket refilled completely; the next call sweeps
    limiter.allow("active")
    assert "gone" not in limiter._buckets and "active" in limiter._buckets


@pytest.mark.parametrize("url, fragment", [
    ("http://169.254.169.254/latest/meta-data", "must use HTTPS"),
    ("https://169.254.169.254/", "private, loopback, link-local, or reserved"),
    ("https://10.0.0.5/mcp", "private, loopback"),
    ("https://127.0.0.1/mcp", "private, loopback"),
    ("https://[::1]/mcp", "private, loopback"),
    ("https://100.64.0.1/mcp", "private, loopback"),  # carrier-grade NAT
    ("http://example.com/mcp", "must use HTTPS"),
    ("https://user:pw@1.1.1.1/mcp", "credential fields"),
    ("https://1.1.1.1/mcp?x=1", "Query strings"),
    ("ftp://1.1.1.1/mcp", "HTTP or HTTPS"),
])
def test_dangerous_upstream_urls_are_rejected(monkeypatch, url, fragment):
    monkeypatch.setenv("INTERNAL_UPSTREAM_HOSTS", "urumi-woocommerce-mcp")
    assert fragment in (validate_upstream_url(url) or "")


def test_internal_service_names_and_public_https_pass(monkeypatch):
    monkeypatch.setenv("INTERNAL_UPSTREAM_HOSTS", "urumi-woocommerce-mcp,urumi-store-info-mcp")
    assert validate_upstream_url("http://urumi-woocommerce-mcp:8000/mcp") is None   # exact allow-list, plain HTTP ok
    assert validate_upstream_url("http://Urumi-Store-Info-MCP:8000/mcp") is None    # case-insensitive host
    assert validate_upstream_url("https://1.1.1.1/mcp") is None                      # public address, no DNS needed
    assert validate_upstream_url("http://urumi-postgres:5432/") == "External MCP endpoints must use HTTPS"
