"""
Fetches and caches the Keycloak realm public key for JWT verification,
and provides an admin token helper for Keycloak Admin REST API calls.
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


def get_admin_token() -> str:
    """
    Obtain a short-lived admin token from Keycloak using the admin credentials
    stored in settings.

    Uses KEYCLOAK_ADMIN_REALM (default: 'master') because admin-cli is a
    built-in client of Keycloak's master realm, not the application realm.

    Raises ValueError with a human-readable message on failure so callers
    can return a proper 503 instead of letting a raw exception cause a 500.
    """
    # admin-cli lives in the master realm, not in the application realm.
    admin_realm = getattr(settings, "KEYCLOAK_ADMIN_REALM", "master")
    admin_client_id = getattr(settings, "KEYCLOAK_ADMIN_CLIENT_ID", "admin-cli")

    url = f"{settings.KEYCLOAK_URL}/realms/{admin_realm}/protocol/openid-connect/token"
    data = {
        "grant_type": "password",
        "client_id": admin_client_id,
        "username": settings.KEYCLOAK_ADMIN_USERNAME,
        "password": settings.KEYCLOAK_ADMIN_PASSWORD,
    }
    client_secret = getattr(settings, "KEYCLOAK_ADMIN_CLIENT_SECRET", None)
    if client_secret:
        data["client_secret"] = client_secret

    try:
        resp = requests.post(url, data=data, timeout=10)
        resp.raise_for_status()
        return resp.json()["access_token"]
    except requests.exceptions.RequestException as exc:
        raise ValueError(
            f"Failed to obtain Keycloak admin token "
            f"(realm={admin_realm}, client={admin_client_id}): {exc}"
        ) from exc