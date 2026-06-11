"""Tests for secure client-IP resolution behind reverse proxies."""

from __future__ import annotations

from ipaddress import ip_address, ip_network

from starlette.requests import Request

from personal_health_mcp.web.clientip import client_ip, ip_allowed


def _request(peer: str = "1.2.3.4", headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/login",
        "headers": raw,
        "client": (peer, 12345),
    }
    return Request(scope)


DOCKER = [ip_network("172.16.0.0/12")]


def test_no_trusted_proxies_uses_peer():
    req = _request(peer="203.0.113.7", headers={"x-forwarded-for": "9.9.9.9"})
    assert client_ip(req, []) == ip_address("203.0.113.7")


def test_untrusted_peer_ignores_forwarded_header():
    # Peer not in trusted set -> forwarded headers are not honored (anti-spoof).
    req = _request(peer="8.8.8.8", headers={"x-forwarded-for": "10.0.0.1"})
    assert client_ip(req, DOCKER) == ip_address("8.8.8.8")


def test_trusted_proxy_uses_forwarded_client():
    req = _request(peer="172.20.0.2", headers={"x-forwarded-for": "203.0.113.7"})
    assert client_ip(req, DOCKER) == ip_address("203.0.113.7")


def test_spoofed_leftmost_xff_is_ignored():
    # Attacker injects a fake leftmost entry; the trusted proxy appends the real
    # client on the right. Right-to-left peeling returns the real client.
    req = _request(
        peer="172.20.0.2",
        headers={"x-forwarded-for": "1.1.1.1, 203.0.113.7"},
    )
    assert client_ip(req, DOCKER) == ip_address("203.0.113.7")


def test_cf_connecting_ip_preferred_when_peer_trusted():
    req = _request(
        peer="172.20.0.2",
        headers={"cf-connecting-ip": "198.51.100.9", "x-forwarded-for": "1.1.1.1"},
    )
    assert client_ip(req, DOCKER) == ip_address("198.51.100.9")


def test_unparseable_peer_returns_none():
    # httpx's ASGI default peer is the non-IP string "testclient".
    assert client_ip(_request(peer="testclient"), []) is None


def test_ip_allowed():
    allowed = [ip_network("10.0.0.0/8"), ip_network("127.0.0.0/8")]
    assert ip_allowed(ip_address("10.1.2.3"), allowed)
    assert ip_allowed(ip_address("127.0.0.1"), allowed)
    assert not ip_allowed(ip_address("8.8.8.8"), allowed)
    assert not ip_allowed(None, allowed)
