"""
JWT authentication dependency for FastAPI endpoints.
Ensures the caller holds a bank_agent or admin realm role.

Mirrors the pattern used by the Django orchestrator's keycloak_client.py
so behaviour is consistent across services.
"""
import os
import logging
from typing import Annotated

import jwt
import httpx
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import hmac
import hashlib
import time

logger = logging.getLogger(__name__)

# ── Env vars (shared with Django via backend/.env) ───────────────────────────
KEYCLOAK_URL    = os.getenv("KEYCLOAK_URL", "").rstrip("/")
KEYCLOAK_REALM  = os.getenv("KEYCLOAK_REALM", "")
# Defensive: if KEYCLOAK_URL doesn't end with /auth, add it
# Keycloak is configured with KC_HTTP_RELATIVE_PATH=/auth
if KEYCLOAK_URL and not KEYCLOAK_URL.endswith("/auth"):
    KEYCLOAK_URL = KEYCLOAK_URL + "/auth"
KEYCLOAK_CLIENT = os.getenv("KEYCLOAK_CLIENT_ID", "")
# KEYCLOAK_ISSUER is the *public* base URL (e.g. http://localhost/auth)
KEYCLOAK_ISSUER = os.getenv("KEYCLOAK_ISSUER", "")

# ── Public key cache (reset on container restart) ─────────────────────────────
_public_key_cache: str | None = None

# FastAPI bearer extractor (non-auto: returns None instead of 401 when absent)
_bearer = HTTPBearer(auto_error=False)

# ── Roles that grant bank-level access ───────────────────────────────────────
BANK_AGENT_ROLES = frozenset({"bank_agent", "admin"})

# ── HMAC Signing for Reports ──────────────────────────────────────────────────

def get_shared_secret() -> str:
    return os.getenv("DJANGO_SECRET_KEY", "fallback-secret-for-dev")

def generate_report_signature(filename: str) -> str:
    """Generate an HMAC-SHA256 signature for a filename.

    Note: signatures are permanent for a file name (no expiry).
    """
    msg = f"{filename}".encode()
    return hmac.new(get_shared_secret().encode(), msg, hashlib.sha256).hexdigest()


def verify_report_signature(filename: str, signature: str) -> bool:
    """Verify the signature for a filename. Does not check expiration.

    Returns True if the signature matches.
    """
    expected = generate_report_signature(filename)
    return hmac.compare_digest(expected, signature)


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
    # The issuer the token was issued by (internal KC URL used at sign time)
    # and the issuer we validate against must match.
    # KC signs with KEYCLOAK_URL/realms/{realm}; public issuer via KEYCLOAK_ISSUER.
    issuer = f"{KEYCLOAK_ISSUER}/realms/{KEYCLOAK_REALM}" if KEYCLOAK_ISSUER else f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"
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


# ── FastAPI dependency ────────────────────────────────────────────────────────

async def require_bank_agent(
    creds: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer),
    ],
) -> dict:
    """
    FastAPI dependency: caller must present a valid JWT that carries the
    'bank_agent' or 'admin' realm role.

    Raises:
        401 — missing / expired / invalid token
        403 — valid token but insufficient role
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
        logger.error("Fraud-service JWT setup error: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Authentication service temporarily unavailable.",
        )
    except Exception as exc:
        logger.warning("Unexpected JWT validation error: %s", exc)
        raise HTTPException(status_code=401, detail="Could not validate credentials.")

    if not (_extract_roles(payload) & BANK_AGENT_ROLES):
        raise HTTPException(
            status_code=403,
            detail="Access denied: 'bank_agent' or 'admin' realm role required.",
        )

    return payload


async def require_admin(
    creds: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer),
    ],
) -> dict:
    """
    FastAPI dependency: caller must present a valid JWT with the 'admin' realm role.
    Required for all write operations on fraud rules (create, update, delete).

    Raises:
        401 — missing / expired / invalid token
        403 — valid token but not admin
    """
    payload = await require_bank_agent(creds)
    if "admin" not in _extract_roles(payload):
        raise HTTPException(
            status_code=403,
            detail="Access denied: 'admin' realm role required for rule management.",
        )
    return payload
