import hashlib
import hmac
import secrets

KEY_PREFIX = "rtg_live_"
PREFIX_LENGTH = 8


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def generate_key() -> tuple[str, str, str]:
    """A new store key: (full key shown once, lookup prefix, SHA-256 to store)."""
    prefix = secrets.token_hex(PREFIX_LENGTH // 2)
    key = f"{KEY_PREFIX}{prefix}_{secrets.token_urlsafe(32)}"
    return key, prefix, hash_key(key)


def parse_prefix(key: str) -> str | None:
    if not key.startswith(KEY_PREFIX):
        return None
    prefix, sep, secret = key[len(KEY_PREFIX):].partition("_")
    if not sep or not secret or len(prefix) != PREFIX_LENGTH:
        return None
    return prefix


def key_matches(key: str, key_hash: str) -> bool:
    return hmac.compare_digest(hash_key(key), key_hash)
