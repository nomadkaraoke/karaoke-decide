"""Tests for the Spotify embed-page playlist reader."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from karaoke_decide.services import spotify_embed as se

FIXTURE = Path(__file__).parent.parent / "fixtures" / "spotify_embed" / "playlist.html"
PID = "37i9dQZF1DXcBWIGoYBM5M"


@pytest.mark.parametrize(
    "value",
    [
        f"https://open.spotify.com/playlist/{PID}?si=abc123",
        f"https://open.spotify.com/intl-de/playlist/{PID}",
        f"spotify:playlist:{PID}",
        PID,
        f"Check out my mix! https://open.spotify.com/playlist/{PID}?si=xyz and tell me what you think",
    ],
)
def test_parse_id_variants(value: str) -> None:
    assert se.parse_spotify_playlist_id(value) == PID


@pytest.mark.parametrize(
    "value", ["", "hello", "https://open.spotify.com/album/" + PID, "https://example.com/playlist/" + PID, "short"]
)
def test_parse_id_rejects_other_input(value: str) -> None:
    with pytest.raises(se.InvalidSpotifyPlaylistUrlError):
        se.parse_spotify_playlist_id(value)


def test_parse_embed_fixture() -> None:
    playlist = se.parse_embed_html(FIXTURE.read_text())
    assert playlist.title == "Fixture Karaoke Mix"
    assert playlist.tracks == [
        {"artist": "Adele", "title": "Hello"},
        {"artist": "Muse", "title": "Starlight"},  # "Muse,\xa0Fixture Guest" -> first artist only
        {"artist": "The Strokes", "title": "Machu Picchu"},
        {"artist": "Elton John", "title": "Rocket Man"},
    ]


@pytest.mark.parametrize(
    "html",
    [
        "<html></html>",
        '<script id="__NEXT_DATA__" type="application/json">not json</script>',
        '<script id="__NEXT_DATA__" type="application/json">{"props": {"pageProps": {}}}</script>',
    ],
)
def test_format_changed_raises(html: str) -> None:
    with pytest.raises(se.SpotifyEmbedFormatChangedError):
        se.parse_embed_html(html)


def test_nonempty_track_list_with_zero_parsed_tracks_is_format_change() -> None:
    renamed = [{"name": "Song", "artists": "Artist"}, "not-a-dict"]
    data = {"props": {"pageProps": {"state": {"data": {"entity": {"name": "Mix", "trackList": renamed}}}}}}
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
    with pytest.raises(se.SpotifyEmbedFormatChangedError):
        se.parse_embed_html(html)


def test_genuinely_empty_track_list_is_not_a_format_change() -> None:
    data = {"props": {"pageProps": {"state": {"data": {"entity": {"name": "Empty", "trackList": []}}}}}}
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
    assert se.parse_embed_html(html).tracks == []


def _html_with_tracks(n: int) -> str:
    tracks = [{"title": f"Song {i}", "subtitle": f"Artist {i}"} for i in range(n)]
    data = {"props": {"pageProps": {"state": {"data": {"entity": {"name": "Big", "trackList": tracks}}}}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'


def test_limit_100() -> None:
    assert len(se.parse_embed_html(_html_with_tracks(150)).tracks) == 100
    assert len(se.parse_embed_html(_html_with_tracks(150), limit=5).tracks) == 5


def _mock_http(response: httpx.Response | Exception) -> MagicMock:
    http = MagicMock()
    http.get = AsyncMock(side_effect=response) if isinstance(response, Exception) else AsyncMock(return_value=response)
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=False)
    return http


async def _get(response: httpx.Response | Exception) -> se.SpotifyEmbedPlaylist:
    http = _mock_http(response)
    with patch.object(se.httpx, "AsyncClient", return_value=http):
        playlist = await se.SpotifyEmbedClient("https://example.test/").get_playlist(PID, limit=500)
    http.get.assert_awaited_once()
    assert http.get.await_args.args[0] == f"https://example.test/embed/playlist/{PID}"
    return playlist


@pytest.mark.asyncio
async def test_client_fetches_and_parses() -> None:
    playlist = await _get(httpx.Response(200, text=_html_with_tracks(150)))
    assert len(playlist.tracks) == 100  # limit capped at the embed's 100


@pytest.mark.asyncio
async def test_client_404_is_not_found() -> None:
    with pytest.raises(se.SpotifyPlaylistNotFoundError):
        await _get(httpx.Response(404))


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [httpx.Response(500), httpx.ConnectError("boom")])
async def test_client_network_and_5xx_are_fetch_errors(response: httpx.Response | Exception) -> None:
    with pytest.raises(se.SpotifyEmbedFetchError):
        await _get(response)


def test_canary_fails_with_reason_under_10_tracks(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.object(
        se.SpotifyEmbedClient, "get_playlist", AsyncMock(return_value=se.SpotifyEmbedPlaylist("x", [{}] * 9))
    ):
        assert se.main(["m", "canary", PID]) == 1
    assert "only 9 tracks" in capsys.readouterr().out


def test_canary_passes_with_10_tracks(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.object(
        se.SpotifyEmbedClient, "get_playlist", AsyncMock(return_value=se.SpotifyEmbedPlaylist("x", [{}] * 10))
    ):
        assert se.main(["m", "canary", PID]) == 0
    assert "ok: 10 tracks" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("subtitle", "expected"),
    [
        ("Earth, Wind & Fire", "Earth, Wind & Fire"),
        ("Tyler, The Creator", "Tyler, The Creator"),
        ("Muse,\xa0Fixture Guest", "Muse"),
        ("Tyler, The Creator,\xa0Guest", "Tyler, The Creator"),
    ],
)
def test_first_artist_splits_only_on_embed_separator(subtitle: str, expected: str) -> None:
    assert se._first_artist(subtitle) == expected
