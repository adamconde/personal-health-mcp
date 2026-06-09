"""Symmetric encryption for secrets at rest.

Wraps Fernet/MultiFernet so OAuth tokens and provider client secrets are stored
encrypted in the database. Configured with one or more keys (comma-separated,
newest first) to support zero-downtime key rotation: new writes use the first
key, reads still succeed with any key.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, MultiFernet


class Crypto:
    """Encrypt/decrypt helper backed by :class:`MultiFernet`.

    Args:
        keys: One or more url-safe base64 Fernet keys, newest first.

    Raises:
        ValueError: If no keys are provided.
    """

    def __init__(self, keys: list[str]) -> None:
        cleaned = [k.strip() for k in keys if k and k.strip()]
        if not cleaned:
            raise ValueError(
                "No encryption keys configured. Set TOKEN_ENC_KEY "
                "(generate with: python -c \"from cryptography.fernet import "
                'Fernet; print(Fernet.generate_key().decode())").'
            )
        self._mf = MultiFernet([Fernet(k.encode()) for k in cleaned])

    @classmethod
    def from_env_value(cls, value: str) -> Crypto:
        """Build from a comma-separated ``TOKEN_ENC_KEY`` string."""
        return cls(value.split(","))

    def encrypt(self, plaintext: str | None) -> str | None:
        """Encrypt ``plaintext`` to a token string; ``None`` passes through."""
        if plaintext is None:
            return None
        return self._mf.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str | None) -> str | None:
        """Decrypt a token string back to plaintext; ``None`` passes through."""
        if token is None:
            return None
        return self._mf.decrypt(token.encode()).decode()
