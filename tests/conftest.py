"""Shared pytest fixtures."""

from __future__ import annotations

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from personal_health_mcp.config import Settings
from personal_health_mcp.crypto import Crypto
from personal_health_mcp.storage import Store


@pytest.fixture
def enc_key() -> str:
    """A throwaway Fernet key for tests."""
    return Fernet.generate_key().decode()


@pytest.fixture
def crypto(enc_key: str) -> Crypto:
    return Crypto([enc_key])


@pytest.fixture
def settings(enc_key: str) -> Settings:
    """Settings with no env provider credentials by default."""
    return Settings(
        public_base_url="https://health.test",
        mcp_auth_token="test-mcp-token",
        web_password="hunter2",
        session_secret="test-session-secret",
        token_enc_key=enc_key,
        database_path=":memory:",
        cookie_secure=False,  # tests run over plain HTTP
    )


@pytest_asyncio.fixture
async def store(tmp_path, settings: Settings, crypto: Crypto):
    """A Store backed by a temporary on-disk SQLite database."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    s = Store(settings=settings, crypto=crypto, database_url=db_url)
    await s.init_models()
    try:
        yield s
    finally:
        await s.dispose()
