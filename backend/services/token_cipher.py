"""At-rest encryption for stored OAuth tokens (Fernet).

Values are stored as ``enc:v1:<fernet token>``. Anything without that prefix is treated as
legacy plaintext and returned unchanged, so existing rows keep working. With no key configured
the cipher is the identity function (tokens stay plaintext, as before ``DECIDE_TOKEN_KEY``).
"""

import logging
from functools import lru_cache

from cryptography.fernet import Fernet

from backend.config import BackendSettings

logger = logging.getLogger(__name__)


class TokenCipher:
    """Encrypt/decrypt token strings with a Fernet key."""

    PREFIX = "enc:v1:"

    def __init__(self, key: str | None) -> None:
        key = (key or "").strip()
        self._fernet: Fernet | None = None
        if key:
            try:
                self._fernet = Fernet(key.encode())
            except (ValueError, TypeError):
                # A malformed key must not 500 every caller: run with encryption off (values pass
                # through) and say so loudly. Decrypting ``enc:v1:`` values still raises ValueError.
                logger.error("DECIDE_TOKEN_KEY is not a valid Fernet key; token encryption is disabled")

    @property
    def enabled(self) -> bool:
        return self._fernet is not None

    def encrypt(self, value: str | None) -> str | None:
        if value is None or self._fernet is None:
            return value
        if value.startswith(self.PREFIX):
            return value
        return self.PREFIX + self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str | None) -> str | None:
        if value is None or not value.startswith(self.PREFIX):
            return value
        if self._fernet is None:
            raise ValueError("Encrypted token found but DECIDE_TOKEN_KEY is not configured")
        return self._fernet.decrypt(value[len(self.PREFIX) :].encode()).decode()


@lru_cache(maxsize=4)
def _cipher_for_key(key: str | None) -> TokenCipher:
    return TokenCipher(key)


def get_token_cipher(settings: BackendSettings) -> TokenCipher:
    """One cipher per distinct key, so a malformed key logs its ERROR once per process."""
    return _cipher_for_key(settings.decide_token_key)
