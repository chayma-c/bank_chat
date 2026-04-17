"""
DRF authentication backend that validates Keycloak-issued JWT tokens.
"""
from urllib import request

import jwt
from django.conf import settings
from django.contrib.auth.models import User
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .keycloak_client import get_public_key


class KeycloakAuthentication(BaseAuthentication):

    def authenticate_header(self, request):
        # DRF calls this to decide 401 vs 403 for unauthenticated requests.
        # Returning a non-None string → 401 Unauthorized with WWW-Authenticate header.
        # Returning None (the default) → DRF silently downgrades to 403 Forbidden.
        return 'Bearer realm="api"'

    def authenticate(self, request):
        print("===== AUTH CALLED =====")
        print("Authorization header:", request.headers.get("Authorization"))    
        header = request.headers.get("Authorization", "")
        print(f"🔐 Auth header present: {bool(header)}")
        print(f"🔐 Auth header starts with Bearer: {header.startswith('Bearer ')}")

        if not header or not header.startswith("Bearer "):
            print("🔐 No Bearer token found, skipping")
            return None

        token = header[7:]
        print(f"🔐 Token (first 50 chars): {token[:50]}...")

        try:
            public_key = get_public_key()
            print(f"🔐 Public key loaded: {public_key[:40]}...")

            # First, decode WITHOUT verification to see what's in the token
            unverified = jwt.decode(token, options={"verify_signature": False})
            print(f"🔐 Token audience (aud): {unverified.get('aud')}")
            print(f"🔐 Token issuer (iss): {unverified.get('iss')}")
            print(f"🔐 Token azp: {unverified.get('azp')}")
            print(f"🔐 Expected audience: {settings.KEYCLOAK_CLIENT_ID}")

            # Now decode WITH verification
            payload = jwt.decode(
                token,
                public_key,
                algorithms=["RS256"],
                audience=settings.KEYCLOAK_CLIENT_ID,
                issuer=f"{settings.KEYCLOAK_ISSUER}/realms/{settings.KEYCLOAK_REALM}",
                options={"verify_exp": True},
            )
            print(f"🔐 ✅ Token valid! User: {payload.get('preferred_username')}")

        except jwt.ExpiredSignatureError:
            print("🔐 ❌ Token expired")
            raise AuthenticationFailed("Token has expired.")
        except jwt.InvalidAudienceError as exc:
            print(f"🔐 ❌ Audience mismatch: {exc}")
            raise AuthenticationFailed(f"Invalid audience: {exc}")
        except jwt.InvalidTokenError as exc:
            print(f"🔐 ❌ Invalid token: {exc}")
            raise AuthenticationFailed(f"Invalid token: {exc}")

        # Sync the Django user (for session/admin compatibility) but expose
        # the enriched payload dict as request.user so all permission classes
        # (which do isinstance(request.user, dict)) work correctly.
        self._get_or_create_django_user(payload)
        user_dict = self._build_user_dict(payload)
        print("AUTH HEADER:", request.headers.get("Authorization"))

        return (user_dict, payload)

    @staticmethod
    def _get_or_create_django_user(payload: dict) -> User:
        """Keep the Django user table in sync (needed for admin/session)."""
        sub = payload["sub"]
        username = payload.get("preferred_username", sub)
        user, _ = User.objects.get_or_create(
            username=username,
            defaults={
                "email":      payload.get("email", ""),
                "first_name": payload.get("given_name", ""),
                "last_name":  payload.get("family_name", ""),
            },
        )
        return user

    @staticmethod
    def _build_user_dict(payload: dict) -> dict:
        """
        Build a plain dict from the JWT payload that all permission classes
        can introspect. Permission classes check isinstance(request.user, dict)
        and then read keys like 'roles', 'realm_access', 'resource_access', etc.
        """
        # Realm-level roles
        realm_roles = payload.get("realm_access", {}).get("roles", [])

        # Client-level roles (e.g. realm-management roles for admin users)
        resource_access = payload.get("resource_access", {})
        client_roles = (
            resource_access
            .get(settings.KEYCLOAK_CLIENT_ID, {})
            .get("roles", [])
        )

        # Flat combined roles list (used by HasRole, IsModerator, etc.)
        all_roles = list(set(realm_roles + client_roles))

        return {
            # Identity
            "user_id":    payload.get("sub"),
            "username":   payload.get("preferred_username"),
            "email":      payload.get("email", ""),
            "first_name": payload.get("given_name", ""),
            "last_name":  payload.get("family_name", ""),
            # Auth flag checked by IsAuthenticated
            "is_authenticated": True,
            # Roles — flat list used by most permission classes
            "roles":         all_roles,
            "realm_roles":   realm_roles,
            "client_roles":  client_roles,
            # Raw Keycloak structures kept for IsAdmin's deep inspection
            "realm_access":    payload.get("realm_access", {}),
            "resource_access": resource_access,
            "groups":          payload.get("groups", []),
        }
#Frontend gets token → backend decodes token → this function maps it to Django user.