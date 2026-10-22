"""Tests for the at-rest token cipher."""

from cryptography.fernet import Fernet

from backend.config import BackendSettings
from backend.services.token_cipher import TokenCipher, get_token_cipher


def _key() -> str:
    return Fernet.generate_key().decode()


def test_round_trip() -> None:
    cipher = TokenCipher(_key())
    enc = cipher.encrypt("tok")
    assert enc is not None and enc.startswith("enc:v1:")
    assert enc != "enc:v1:tok"
    assert cipher.decrypt(enc) == "tok"
    assert cipher.enabled


def test_legacy_plaintext_passthrough() -> None:
    assert TokenCipher(_key()).decrypt("plain") == "plain"


def test_no_key_is_identity() -> None:
    cipher = TokenCipher(None)
    assert not cipher.enabled
    assert cipher.encrypt("tok") == "tok"
    assert cipher.decrypt("tok") == "tok"
    assert TokenCipher("").encrypt("tok") == "tok"


def test_none_passes_through() -> None:
    cipher = TokenCipher(_key())
    assert cipher.encrypt(None) is None
    assert cipher.decrypt(None) is None


def test_get_token_cipher_uses_settings_key() -> None:
    key = _key()
    cipher = get_token_cipher(BackendSettings(decide_token_key=key))
    assert cipher.enabled
    assert cipher.decrypt(cipher.encrypt("x")) == "x"
    assert not get_token_cipher(BackendSettings(decide_token_key="")).enabled
