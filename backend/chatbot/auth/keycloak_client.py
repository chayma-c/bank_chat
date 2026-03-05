"""
Fetches and caches the Keycloak realm public key for JWT verification.
"""
import requests
from django.conf import settings

_public_key_cache: str | None = None


def get_public_key() -> str:
    """Return the PEM-formatted RSA public key for the configured realm."""
    global _public_key_cache
    if _public_key_cache:
        return _public_key_cache

    url = f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()

    raw = resp.json()["public_key"]
    _public_key_cache = f"-----BEGIN PUBLIC KEY-----\n{raw}\n-----END PUBLIC KEY-----"
    return _public_key_cache


def clear_cache() -> None:
    """Reset the cached key (useful for tests or key rotation)."""
    global _public_key_cache
    _public_key_cache = None