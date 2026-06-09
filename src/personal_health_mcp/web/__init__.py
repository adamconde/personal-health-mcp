"""Web UI package.

Exposes :func:`create_web_routes`, which returns the Starlette routes for the
single-user web interface (auth, dashboard, providers, metrics, units) plus the
OAuth callback routes and an unauthenticated ``/healthz`` liveness endpoint.
"""

from __future__ import annotations

from .routes import create_web_routes

__all__ = ["create_web_routes"]
