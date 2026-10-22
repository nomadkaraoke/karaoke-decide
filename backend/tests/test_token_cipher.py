"""Tests for the at-rest token cipher."""

import logging

import pytest
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


def test_decrypt_encrypted_value_without_key_raises() -> None:
    enc = TokenCipher(_key()).encrypt("tok")
    with pytest.raises(ValueError, match="DECIDE_TOKEN_KEY"):
        TokenCipher(None).decrypt(enc)


def test_encrypt_is_idempotent_on_encrypted_values() -> None:
    cipher = TokenCipher(_key())
    enc = cipher.encrypt("tok")
    assert cipher.encrypt(enc) == enc
    assert cipher.decrypt(cipher.encrypt(enc)) == "tok"


def test_malformed_key_disables_cipher_and_logs_one_error(caplog: pytest.LogCaptureFixture) -> None:
    enc = TokenCipher(_key()).encrypt("tok")
    with caplog.at_level(logging.ERROR, logger="backend.services.token_cipher"):
        cipher = TokenCipher("not-a-fernet-key")
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "DECIDE_TOKEN_KEY" in errors[0].getMessage()
    assert not cipher.enabled
    assert cipher.encrypt("tok") == "tok"
    assert cipher.decrypt("plain") == "plain"
    with pytest.raises(ValueError):
        cipher.decrypt(enc)
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1


def test_get_token_cipher_malformed_key_logs_once_per_process(caplog: pytest.LogCaptureFixture) -> None:
    settings = BackendSettings(decide_token_key="still-not-a-key")
    with caplog.at_level(logging.ERROR, logger="backend.services.token_cipher"):
        first, second = get_token_cipher(settings), get_token_cipher(settings)
    assert first is second and not first.enabled
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) <= 1
