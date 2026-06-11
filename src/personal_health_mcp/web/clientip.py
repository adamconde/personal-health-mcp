"""Secure client-IP resolution behind reverse proxies.

The break-glass password login is gated on the client's IP, so the IP must be
the *real* remote client — not the reverse proxy in front of the app. Forwarded
headers (``X-Forwarded-For``, ``CF-Connecting-IP``) are client-spoofable, so we
only trust them when the direct peer is itself a configured trusted proxy, and
we peel ``X-Forwarded-For`` right-to-left (stopping at the first untrusted hop)
so an injected leftmost entry can't beat the gate.
"""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address

from starlette.requests import Request

IPAddr = IPv4Address | IPv6Address
IPNet = IPv4Network | IPv6Network


def _in_any(ip: IPAddr, nets: list[IPNet]) -> bool:
    """Return True if ``ip`` falls within any of ``nets``."""
    return any(ip in net for net in nets)


def _parse(value: str) -> IPAddr | None:
    """Parse an IP string, returning None if it isn't a valid address."""
    try:
        return ip_address(value.strip())
    except ValueError:
        return None


def client_ip(request: Request, trusted_proxies: list[IPNet]) -> IPAddr | None:
    """Resolve the real client IP for ``request``.

    Args:
        request: The incoming request.
        trusted_proxies: Networks of reverse proxies whose forwarded headers we
            trust. Empty means trust none (use the direct peer address).

    Returns:
        The client's IP address, or ``None`` if it can't be determined (e.g. a
        non-IP test peer). A ``None`` result should be treated as "not allowed".
    """
    peer = _parse(request.client.host) if request.client else None
    if peer is None:
        return None
    # Only consult forwarded headers when the direct peer is a trusted proxy.
    if not trusted_proxies or not _in_any(peer, trusted_proxies):
        return peer
    # Cloudflare's connecting-IP is the canonical real-client header for the
    # tunnel path; prefer it when the peer (cloudflared) is trusted.
    cf = _parse(request.headers.get("cf-connecting-ip", ""))
    if cf is not None:
        return cf
    # Otherwise peel X-Forwarded-For from the right, skipping trusted hops; the
    # first untrusted address is what the last trusted proxy received from.
    xff = request.headers.get("x-forwarded-for", "")
    for part in reversed(xff.split(",")):
        candidate = _parse(part)
        if candidate is not None and not _in_any(candidate, trusted_proxies):
            return candidate
    return peer  # every hop was trusted (or unparseable); best effort


def ip_allowed(ip: IPAddr | None, allowed: list[IPNet]) -> bool:
    """Return True if ``ip`` is non-None and within ``allowed``."""
    return ip is not None and _in_any(ip, allowed)
