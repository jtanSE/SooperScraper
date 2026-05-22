from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class SecretKeyMissing(RuntimeError):
    """Raised when credential encryption is required but no key is configured."""


def _help_message() -> str:
    sample = Fernet.generate_key().decode()
    return (
        "SOOPERSCRAPER_SECRET_KEY is not set. Generate one and set it before "
        "storing credentials, e.g.:\n"
        f"  $env:SOOPERSCRAPER_SECRET_KEY = '{sample}'\n"
        "Keep this key safe — losing it makes existing stored credentials "
        "unrecoverable."
    )


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = os.environ.get("SOOPERSCRAPER_SECRET_KEY")
    if not key:
        raise SecretKeyMissing(_help_message())
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as exc:
        raise SecretKeyMissing(f"SOOPERSCRAPER_SECRET_KEY is not a valid Fernet key: {exc}") from exc


def reset_for_tests() -> None:
    """Clear the cached Fernet so tests can change the key mid-process."""
    _fernet.cache_clear()


def encrypt_json(payload: dict[str, Any]) -> bytes:
    return _fernet().encrypt(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def decrypt_json(token: bytes) -> dict[str, Any]:
    try:
        plain = _fernet().decrypt(token)
    except InvalidToken as exc:
        raise SecretKeyMissing(
            "Stored credentials cannot be decrypted with the current "
            "SOOPERSCRAPER_SECRET_KEY. Either restore the original key or "
            "re-enter credentials for affected jobs."
        ) from exc
    return json.loads(plain.decode("utf-8"))
