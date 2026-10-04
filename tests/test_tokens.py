import jwt
import pytest

from esl.auth.tokens import InvalidToken
from tests.auth import OTHER_KEY, make_token, make_verifier


def test_valid_access_token_returns_sub():
    assert make_verifier().verify(make_token("sub-123")) == "sub-123"


@pytest.mark.parametrize("token", [
    make_token("s", expires_in=-120),                 # expired (beyond leeway)
    make_token("s", issuer="https://evil.example"),   # wrong issuer
    make_token("s", token_use="id"),                  # ID token, not access
    make_token("s", client_id="someone-elses-app"),   # other app client
    make_token("s", key=OTHER_KEY),                   # bad signature
    "not-a-jwt",
])
def test_rejected_tokens(token):
    with pytest.raises(InvalidToken):
        make_verifier().verify(token)


def test_unsigned_token_rejected():
    token = jwt.encode({"sub": "s", "token_use": "access"}, key=None, algorithm="none")
    with pytest.raises(InvalidToken):
        make_verifier().verify(token)
