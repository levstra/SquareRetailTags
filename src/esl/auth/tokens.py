from collections.abc import Callable
from typing import Any

import jwt


class InvalidToken(Exception):
    """The bearer token is missing, malformed, expired, or not ours."""


class SignInUnavailable(Exception):
    """The identity provider's signing keys couldn't be fetched."""


KeyResolver = Callable[[str], Any]


class TokenVerifier:
    """Verifies Cognito *access* tokens for one app client and returns `sub`."""

    def __init__(self, *, issuer: str, client_id: str, key_resolver: KeyResolver):
        self._issuer = issuer
        self._client_id = client_id
        self._key_resolver = key_resolver

    def verify(self, token: str) -> str:
        try:
            key = self._key_resolver(token)
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self._issuer,
                leeway=30,
                # Access tokens carry client_id, not aud.
                options={"require": ["exp", "iat", "iss", "sub"], "verify_aud": False},
            )
        except jwt.PyJWKClientConnectionError as exc:
            raise SignInUnavailable(str(exc)) from exc
        except jwt.PyJWTError as exc:
            raise InvalidToken(str(exc)) from exc
        if claims.get("token_use") != "access":
            raise InvalidToken("not an access token")
        if claims.get("client_id") != self._client_id:
            raise InvalidToken("token was issued to another app client")
        return claims["sub"]


def cognito_verifier(region: str, user_pool_id: str, client_id: str) -> TokenVerifier:
    issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
    jwks = jwt.PyJWKClient(f"{issuer}/.well-known/jwks.json", cache_keys=True, lifespan=3600)
    return TokenVerifier(
        issuer=issuer,
        client_id=client_id,
        key_resolver=lambda token: jwks.get_signing_key_from_jwt(token).key,
    )
