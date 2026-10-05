from cryptography.fernet import Fernet

from esl.config import settings


class TokenCipher:
    """Symmetric encryption for third-party tokens at rest (Fernet)."""

    def __init__(self, key: str):
        self._fernet = Fernet(key.encode())

    def encrypt(self, plain: str) -> str:
        return self._fernet.encrypt(plain.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()


def get_cipher() -> TokenCipher:
    if not settings.token_encryption_key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not set")
    return TokenCipher(settings.token_encryption_key)
