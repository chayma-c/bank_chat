"""
JWT authentication dependency — mirrors fraud-service/fraud/auth.py exactly.
Only 'bank_agent' and 'admin' realm roles are granted access.
"""
import os
import logging
from typing import Annotated

import jwt
import httpx
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)

# ── Keycloak config (shared env vars with the rest of the platform) ───────────
KEYCLOAK_URL    = os.getenv("KEYCLOAK_URL", "").rstrip("/")
KEYCLOAK_REALM  = os.getenv("KEYCLOAK_REALM", "")
KEYCLOAK_CLIENT = os.getenv("KEYCLOAK_CLIENT_ID", "")
KEYCLOAK_ISSUER = os.getenv("KEYCLOAK_ISSUER", "")

if KEYCLOAK_URL and not KEYCLOAK_URL.endswith("/auth"):
    KEYCLOAK_URL = KEYCLOAK_URL + "/auth"

# ── Role whitelist — administrators and fraud agents only ────────────────────
ALLOWED_ROLES = frozenset({"bank_agent", "admin"})

# ── Public key cache ──────────────────────────────────────────────────────────
_public_key_cache: str | None = None

_bearer = HTTPBearer(auto_error=False)


def _get_public_key() -> str:
    """Fetch and cache the Keycloak realm RSA public key."""
    global _public_key_cache
    if _public_key_cache:
        return _public_key_cache

    if not KEYCLOAK_URL or not KEYCLOAK_REALM:
        raise RuntimeError(
            "KEYCLOAK_URL and KEYCLOAK_REALM must be set for JWT validation."
        )

    url = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"
    try:
        resp = httpx.get(url, timeout=10)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not fetch Keycloak public key: {exc}") from exc

    raw = resp.json().get("public_key", "")
    if not raw:
        raise RuntimeError("Keycloak realm info did not contain a public_key.")

    _public_key_cache = (
        f"-----BEGIN PUBLIC KEY-----\n{raw}\n-----END PUBLIC KEY-----"
    )
    return _public_key_cache


def _decode_token(token: str) -> dict:
    """Decode and fully verify a Keycloak RS256 JWT."""
    key = _get_public_key()
    issuer = (
        f"{KEYCLOAK_ISSUER}/realms/{KEYCLOAK_REALM}"
        if KEYCLOAK_ISSUER
        else f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"
    )
    return jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        audience=KEYCLOAK_CLIENT,
        issuer=issuer,
        options={"verify_exp": True},
    )


def _extract_roles(payload: dict) -> frozenset:
    return frozenset(payload.get("realm_access", {}).get("roles", []))


# ── FastAPI dependency ─────────────────────────────────────────────────────────

async def require_bank_agent(
    creds: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer),
    ],
) -> dict:
    """
    FastAPI dependency: caller must present a valid JWT carrying the
    'bank_agent' or 'admin' realm role.
    Clients (role 'client') are explicitly denied access.

    Raises:
        401 — missing / expired / invalid token
        403 — valid token but insufficient role (e.g. client role)
    """
    if creds is None:
        raise HTTPException(
            status_code=401,
            detail="Authorization header is missing.",
        )

    try:
        payload = _decode_token(creds.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired.")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")
    except RuntimeError as exc:
        logger.error("Text-to-SQL service JWT setup error: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Authentication service temporarily unavailable.",
        )
    except Exception as exc:
        logger.warning("Unexpected JWT validation error: %s", exc)
        raise HTTPException(status_code=401, detail="Could not validate credentials.")

    roles = _extract_roles(payload)
    if not (roles & ALLOWED_ROLES):
        raise HTTPException(
            status_code=403,
            detail="Access denied: 'bank_agent' or 'admin' role required. "
                   "This feature is reserved for bank staff only.",
        )

    return payload
