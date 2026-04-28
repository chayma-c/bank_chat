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
    """Obtain a short-lived admin token from Keycloak."""
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
        raise ValueError(f"Failed to obtain Keycloak admin token: {exc}")

# ── Admin API Helpers ─────────────────────────────────────────────────────────

def list_realm_users() -> list:
    """Fetch the list of all users in the application realm."""
    token = get_admin_token()
    url = f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users"
    headers = {"Authorization": f"Bearer {token}"}
    
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()

def get_user_role_mappings(user_id: str) -> list:
    """Fetch realm-level role mappings for a specific user."""
    token = get_admin_token()
    url = f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users/{user_id}/role-mappings/realm"
    headers = {"Authorization": f"Bearer {token}"}
    
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()

def update_user_role(user_id: str, role_name: str, action: str = "add"):
    """
    Assign or remove a realm role from a user.
    Action must be 'add' or 'remove'.
    """
    token = get_admin_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    
    # 1. Fetch the role object to get its ID (required by Keycloak for assignment)
    role_url = f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/roles/{role_name}"
    role_resp = requests.get(role_url, headers=headers, timeout=10)
    role_resp.raise_for_status()
    role_data = [role_resp.json()] # Payload must be a list of role objects

    # 2. Apply the change
    mapping_url = f"{settings.KEYCLOAK_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users/{user_id}/role-mappings/realm"
    if action == "add":
        resp = requests.post(mapping_url, json=role_data, headers=headers, timeout=10)
    else:
        resp = requests.delete(mapping_url, json=role_data, headers=headers, timeout=10)
    
    resp.raise_for_status()
    return True