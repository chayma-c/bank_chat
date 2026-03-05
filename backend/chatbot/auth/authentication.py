"""
DRF authentication backend that validates Keycloak-issued JWT tokens.
"""
import jwt
from django.conf import settings
from django.contrib.auth.models import User
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .keycloak_client import get_public_key


class KeycloakAuthentication(BaseAuthentication):

    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        print(f"🔐 Auth header present: {bool(header)}")
        print(f"🔐 Auth header starts with Bearer: {header.startswith('Bearer ')}")

        if not header.startswith("Bearer "):
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

        user = self._get_or_create_user(payload)
        return (user, payload)

    @staticmethod
    def _get_or_create_user(payload: dict) -> User:
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