"""Tests for the Apple Music (MusicKit) client: developer token signing and /v1/me reads."""

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jose import jwt

from karaoke_decide.core.config import Settings
from karaoke_decide.services import apple_music
from karaoke_decide.services.apple_music import (
    AppleMusicAuthError,
    AppleMusicClient,
    AppleMusicNotConfiguredError,
    DeveloperTokenSigner,
)


@pytest.fixture(autouse=True)
def clear_token_cache() -> None:
    apple_music._token_cache.clear()


@pytest.fixture(scope="module")
def ec_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _settings(key: ec.EllipticCurvePrivateKey, escaped_newlines: bool = False) -> Settings:
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    if escaped_newlines:  # how a one-line env var may carry the .p8
        pem = pem.replace("\n", "\\n")
    return Settings(apple_musickit_private_key=pem, apple_musickit_key_id="KEYID12345", apple_team_id="TEAMID1234")


def _public_pem(key: ec.EllipticCurvePrivateKey) -> str:
    return (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )


class TestDeveloperTokenSigner:
    def test_signs_es256_with_kid_and_team(self, ec_key: ec.EllipticCurvePrivateKey) -> None:
        token, expires_at = DeveloperTokenSigner(_settings(ec_key)).token()

        assert jwt.get_unverified_header(token) == {"alg": "ES256", "kid": "KEYID12345", "typ": "JWT"}
        claims = jwt.decode(token, _public_pem(ec_key), algorithms=["ES256"])
        assert claims["iss"] == "TEAMID1234"
        assert claims["exp"] == expires_at
        assert claims["exp"] - claims["iat"] <= 15_777_000  # Apple's 6-month maximum
        assert "origin" not in claims

    def test_origin_claim_for_web(self, ec_key: ec.EllipticCurvePrivateKey) -> None:
        token, _ = DeveloperTokenSigner(_settings(ec_key)).token(origins=("https://decide.nomadkaraoke.com",))

        claims = jwt.decode(token, _public_pem(ec_key), algorithms=["ES256"])
        assert claims["origin"] == ["https://decide.nomadkaraoke.com"]

    def test_accepts_escaped_newlines(self, ec_key: ec.EllipticCurvePrivateKey) -> None:
        token, _ = DeveloperTokenSigner(_settings(ec_key, escaped_newlines=True)).token()

        assert jwt.decode(token, _public_pem(ec_key), algorithms=["ES256"])["iss"] == "TEAMID1234"

    def test_caches_across_instances(self, ec_key: ec.EllipticCurvePrivateKey) -> None:
        first, _ = DeveloperTokenSigner(_settings(ec_key)).token()
        second, _ = DeveloperTokenSigner(_settings(ec_key)).token()
        web, _ = DeveloperTokenSigner(_settings(ec_key)).token(origins=("https://x.example",))

        assert first == second
        assert web != first

    def test_not_configured(self) -> None:
        signer = DeveloperTokenSigner(
            Settings(apple_musickit_private_key="", apple_musickit_key_id="", apple_team_id="")
        )

        assert not signer.configured
        with pytest.raises(AppleMusicNotConfiguredError):
            signer.token()


def _song(artist: str, title: str, kind: str = "library-songs", duration: int | None = 200_000) -> dict:
    attrs: dict = {"artistName": artist, "name": title}
    if duration:
        attrs["durationInMillis"] = duration
    return {"id": f"{artist}-{title}", "type": kind, "attributes": attrs}


def _client(ec_key: ec.EllipticCurvePrivateKey, handler) -> AppleMusicClient:
    return AppleMusicClient(
        DeveloperTokenSigner(_settings(ec_key)), http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


class TestAppleMusicClient:
    async def test_sends_both_tokens_and_reads_storefront(self, ec_key: ec.EllipticCurvePrivateKey) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"data": [{"id": "gb", "type": "storefronts"}]})

        assert await _client(ec_key, handler).get_storefront("user-token") == "gb"
        assert seen[0].url.path == "/v1/me/storefront"
        assert seen[0].headers["Music-User-Token"] == "user-token"
        assert seen[0].headers["Authorization"].startswith("Bearer ey")

    async def test_pages_library_songs_until_short_page(self, ec_key: ec.EllipticCurvePrivateKey) -> None:
        offsets: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            offset = request.url.params["offset"]
            offsets.append(offset)
            if offset == "0":
                songs = [_song(f"Artist {i}", f"Song {i}") for i in range(100)]
                return httpx.Response(200, json={"data": songs, "next": "/v1/me/library/songs?offset=100"})
            return httpx.Response(200, json={"data": [_song("Last", "One")]})

        tracks = await _client(ec_key, handler).get_library_songs("t")

        assert offsets == ["0", "100"]
        assert len(tracks) == 101
        assert tracks[-1].artist == "Last" and tracks[-1].duration_ms == 200_000

    async def test_respects_limit(self, ec_key: ec.EllipticCurvePrivateKey) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            songs = [_song(f"A{i}", f"S{i}", kind="songs") for i in range(30)]
            return httpx.Response(200, json={"data": songs, "next": "more"})

        tracks = await _client(ec_key, handler).get_recent_tracks("t", limit=45)

        assert len(tracks) == 45

    async def test_skips_non_songs_and_blank_names(self, ec_key: ec.EllipticCurvePrivateKey) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            data = [
                _song("A", "Song", kind="songs"),
                _song("A", "Video", kind="music-videos"),
                _song("", "No artist", kind="songs"),
                {"id": "x", "type": "songs"},
                _song("B", "No duration", kind="songs", duration=None),
            ]
            return httpx.Response(200, json={"data": data})

        tracks = await _client(ec_key, handler).get_recent_tracks("t")

        assert [(t.artist, t.title, t.duration_ms) for t in tracks] == [
            ("A", "Song", 200_000),
            ("B", "No duration", None),
        ]

    @pytest.mark.parametrize("status", [401, 403])
    async def test_refused_token_raises_auth_error(self, ec_key: ec.EllipticCurvePrivateKey, status: int) -> None:
        with pytest.raises(AppleMusicAuthError):
            await _client(ec_key, lambda r: httpx.Response(status)).get_library_songs("expired")

    async def test_404_is_an_empty_library(self, ec_key: ec.EllipticCurvePrivateKey) -> None:
        assert await _client(ec_key, lambda r: httpx.Response(404)).get_library_songs("t") == []

    async def test_server_error_raises(self, ec_key: ec.EllipticCurvePrivateKey) -> None:
        with pytest.raises(Exception) as info:
            await _client(ec_key, lambda r: httpx.Response(500)).get_storefront("t")
        assert not isinstance(info.value, AppleMusicAuthError)

    async def test_connection_error_raises(self, ec_key: ec.EllipticCurvePrivateKey) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        with pytest.raises(Exception, match="Failed to connect"):
            await _client(ec_key, handler).get_storefront("t")

    async def test_library_ranks_artists_recent_first(self, ec_key: ec.EllipticCurvePrivateKey) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "recent" in request.url.path:
                return httpx.Response(200, json={"data": [_song("Recent Artist", "Hit", kind="songs")]})
            data = [_song("Library Artist", "One"), _song("Library Artist", "Two"), _song("Other", "Three")]
            return httpx.Response(200, json={"data": data})

        library = await _client(ec_key, handler).get_library("t")

        assert [t.title for t in library.recent] == ["Hit"]
        assert len(library.library) == 3
        # Library Artist: 2 library songs; Recent Artist: 1 recent play counted twice; Other: 1
        assert library.artists[2] == "Other"
        assert set(library.artists[:2]) == {"Library Artist", "Recent Artist"}
