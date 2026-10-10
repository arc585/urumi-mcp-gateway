"""API-key hashing and encryption of upstream credentials stored in the database."""

import hashlib
import hmac
import os
import secrets

from cryptography.fernet import Fernet, InvalidToken

KEY_PREFIX = "urm_"


def create_api_key() -> str:
    """A new random API key: "urm_" + 43 URL-safe characters (256 bits of randomness)."""
    return f"{KEY_PREFIX}{secrets.token_urlsafe(32)}"


def _pepper() -> bytes:
    pepper = os.getenv("API_KEY_PEPPER", "")
    if not pepper:
        raise RuntimeError("API_KEY_PEPPER is not configured")
    return pepper.encode()


def hash_api_key(key: str) -> str:
    """HMAC-SHA256 of the key with a server-side secret ("pepper") from a Kubernetes Secret.

    Keys are long random values, so a fast keyed hash is enough (no password-style slow hashing
    needed), and without the pepper a leaked database cannot be used to test guessed keys.
    """
    return hmac.new(_pepper(), key.encode(), hashlib.sha256).hexdigest()


def _fernet() -> Fernet:
    key = os.getenv("UPSTREAM_ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError("UPSTREAM_ENCRYPTION_KEY is not configured")
    return Fernet(key.encode())


def encrypt_upstream_credential(value: str) -> str:
    """Fernet = AES-128-CBC + HMAC-SHA256 with a random IV; ciphertext is tamper-evident."""
    return _fernet().encrypt(value.encode()).decode()


def decrypt_upstream_credential(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("Upstream credential could not be decrypted (wrong or rotated key)") from exc
