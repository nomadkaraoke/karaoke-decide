"""At-rest encryption for stored OAuth tokens (Fernet).

Values are stored as ``enc:v1:<fernet token>``. Anything without that prefix is treated as
legacy plaintext and returned unchanged, so existing rows keep working. With no key configured
the cipher is the identity function (tokens stay plaintext, as before ``DECIDE_TOKEN_KEY``).
"""

from cryptography.fernet import Fernet

from backend.config import BackendSettings


class TokenCipher:
    """Encrypt/decrypt token strings with a Fernet key."""

    PREFIX = "enc:v1:"

    def __init__(self, key: str | None) -> None:
        key = (key or "").strip()
        self._fernet = Fernet(key.encode()) if key else None

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


def get_token_cipher(settings: BackendSettings) -> TokenCipher:
    return TokenCipher(settings.decide_token_key)
