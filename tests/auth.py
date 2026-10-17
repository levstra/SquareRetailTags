"""Signs Cognito-shaped access tokens with a throwaway key. Never talks to AWS."""
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from esl.auth.tokens import TokenVerifier

ISSUER = "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_TESTPOOL"
CLIENT_ID = "test-app-client"
PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_token(
    sub: str,
    *,
    token_use: str = "access",
    client_id: str = CLIENT_ID,
    issuer: str = ISSUER,
    expires_in: int = 300,
    key=PRIVATE_KEY,
) -> str:
    now = int(time.time())
    claims = {
        "sub": sub, "token_use": token_use, "client_id": client_id,
        "iss": issuer, "iat": now, "exp": now + expires_in,
    }
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})


def make_verifier() -> TokenVerifier:
    return TokenVerifier(
        issuer=ISSUER, client_id=CLIENT_ID, key_resolver=lambda _token: PRIVATE_KEY.public_key()
    )


def bearer(sub: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(sub)}"}
