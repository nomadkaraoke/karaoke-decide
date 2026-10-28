"""v2 user context: sources, weights (max wins), kn mapping, exclusions, paging."""

from __future__ import annotations

from typing import Any

import pytest

from backend.services.recs_v2 import weights as w
from backend.services.recs_v2.context import UserContextV2, build_context
from karaoke_decide.catalog.keys import artist_key
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.song_search.text_normalize import song_key

UID = "user_abc"


class FakeFirestore:
    """In-memory stand-in covering the equality filters / limit / offset build_context uses."""

    def __init__(self, **collections: list[dict[str, Any]]) -> None:
        self.cols = collections
        self.song_queries: list[tuple[int | None, int | None]] = []

    async def get_document(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        return next((d for d in self.cols.get(collection, []) if d.get("id") == doc_id), None)

    async def query_documents(
        self,
        collection: str,
        filters: list[tuple[str, str, Any]] | None = None,
        order_by: str | None = None,
        order_direction: str = "ASCENDING",
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        if collection == "user_songs":
            self.song_queries.append((limit, offset))
        rows = [d for d in self.cols.get(collection, []) if all(d.get(f) == v for f, _, v in (filters or []))]
        rows = rows[offset or 0 :]
        return rows[:limit] if limit else rows


class FakeFeedback:
    def __init__(self, hidden: set[str] | None = None) -> None:
        self.hidden = hidden or set()

    async def hidden_keys(self, user_id: str) -> set[str]:
        return self.hidden


def _song(song_id: str, artist: str, title: str, source: str = "quiz", **extra: Any) -> dict[str, Any]:
    return {"user_id": UID, "song_id": song_id, "source": source, "artist": artist, "title": title, **extra}


def _artist(name: str, source: str, rank: int | None = None) -> dict[str, Any]:
    return {"user_id": UID, "artist_name": name, "source": source, "rank": rank}


async def _ctx(reader: CatalogReader, fs: FakeFirestore, hidden: set[str] | None = None) -> UserContextV2:
    return await build_context(fs, reader, FakeFeedback(hidden), UID)  # type: ignore[arg-type]


async def test_max_across_sources(reader: CatalogReader) -> None:
    fs = FakeFirestore(
        decide_users=[
            {
                "id": "h",
                "user_id": UID,
                "quiz_artist_affinities": [{"artist_name": "Toto", "affinity": "love"}],
                "quiz_manual_artists": [{"artist_name": "Weezer"}],
            }
        ],
        user_artists=[_artist("Toto", "spotify", 100), _artist("Weezer", "lastfm", 1), _artist("Abba", "manual")],
        user_songs=[],
    )
    ctx = await _ctx(reader, fs)
    assert ctx.artists[artist_key("Toto")].weight == pytest.approx(w.QUIZ_LOVE)  # 0.9 beats 0.5 (rank 100)
    assert ctx.artists[artist_key("Toto")].source == "quiz"
    assert ctx.artists[artist_key("Weezer")].weight == pytest.approx(w.artist_top(1))  # 0.995 beats 0.8
    assert ctx.artists[artist_key("Abba")].weight == w.ARTIST_MANUAL


def test_weight_table() -> None:
    assert w.artist_top(0) == 1.0 and w.artist_top(100) == 0.5 and w.artist_top(500) == 0.5
    assert w.artist_from_songs(2) == pytest.approx(0.6) and w.artist_from_songs(50) == 0.9
    assert [w.lastfm_song(n) for n in (12, 10, 5, 3, 2, 1, None)] == [1.0, 1.0, 0.7, 0.7, 0.4, 0.4, 0.4]
    assert w.song_weight({"source": "manual"}) == 1.0
    assert w.song_weight({"source": "spotify", "rank": 3}) == 0.8
    assert w.song_weight({"source": "spotify", "rank": None}) == 0.9
    assert w.song_weight({"source": "youtube_music"}) == 0.8
    assert w.song_weight({"source": "apple_music", "playcount": 2}) == 0.8  # recent play
    assert w.song_weight({"source": "apple_music", "playcount": None}) == 0.9  # in the library
    assert w.song_weight({"source": "quiz"}) == w.song_weight({"source": "listenbrainz"}) == 0.6


async def test_artists_of_known_songs(reader: CatalogReader) -> None:
    fs = FakeFirestore(
        user_songs=[_song(f"spotify:{i}", "Some Band", f"Tune {i}", source="spotify") for i in range(3)],
    )
    ctx = await _ctx(reader, fs)
    sig = ctx.artists[artist_key("Some Band")]
    assert sig.weight == pytest.approx(0.7) and sig.source == "songs" and sig.name == "Some Band"


async def test_excluded_artist_removed(reader: CatalogReader) -> None:
    fs = FakeFirestore(
        decide_users=[{"id": "h", "user_id": UID, "excluded_artists": ["toto"]}],
        user_artists=[_artist("Toto", "spotify", 1), _artist("Weezer", "spotify", 2)],
        user_songs=[_song("1", "Toto", "Africa")],
    )
    ctx = await _ctx(reader, fs)
    assert artist_key("Toto") not in ctx.artists and artist_key("Weezer") in ctx.artists
    assert ctx.known_songs  # excluded artist removes the artist signal, not the known song


async def test_kn_id_maps_to_song_key(reader: CatalogReader) -> None:
    fs = FakeFirestore(user_songs=[_song("1", "", "", source="manual")])  # kn_id 1 = Toto - Africa
    ctx = await _ctx(reader, fs)
    assert ctx.known_songs == {song_key("Toto", "Africa"): w.SONG_MANUAL}
    assert ctx.known_without_karaoke == []


async def test_spotify_id_song_maps_by_artist_title(reader: CatalogReader) -> None:
    fs = FakeFirestore(
        user_songs=[_song("spotify:xyz", "Weezer", "Buddy Holly", source="spotify", rank=4)],
    )
    ctx = await _ctx(reader, fs)
    assert ctx.known_songs == {song_key("Weezer", "Buddy Holly"): w.SONG_SPOTIFY_TOP}
    assert ctx.known_without_karaoke == []  # the catalog has a karaoke version


async def test_no_karaoke_known_song_listed(reader: CatalogReader) -> None:
    fs = FakeFirestore(user_songs=[_song("lastfm:a:b", "Nobody Band", "Unmade Song", source="lastfm", playcount=12)])
    ctx = await _ctx(reader, fs)
    k = song_key("Nobody Band", "Unmade Song")
    assert ctx.known_songs == {k: 1.0}
    assert ctx.known_without_karaoke == [(k, "Nobody Band", "Unmade Song")]


async def test_hidden_loaded(reader: CatalogReader) -> None:
    ctx = await _ctx(reader, FakeFirestore(), hidden={"a|b", "c|d"})
    assert ctx.hidden == {"a|b", "c|d"}


async def test_cold_and_has_profile(reader: CatalogReader) -> None:
    fs = FakeFirestore(
        decide_users=[
            {
                "id": "h",
                "user_id": UID,
                "quiz_genres": ["Rock"],
                "quiz_decade_pref": "1990s",
                "quiz_energy_pref": "high",
                "quiz_crowd_pleaser_pref": "hits",
            }
        ]
    )
    ctx = await _ctx(reader, fs)
    assert ctx.has_profile and ctx.is_cold
    assert ctx.genres == {"rock"} and ctx.decades == {"1990s"}
    assert ctx.energy_pref == "high" and ctx.crowd_pref == "hits"
    empty = await _ctx(reader, FakeFirestore())
    assert not empty.has_profile and empty.is_cold


async def test_guest_doc_by_id(reader: CatalogReader) -> None:
    fs = FakeFirestore(decide_users=[{"id": "guest_1", "quiz_genres": ["pop"]}])
    ctx = await build_context(fs, reader, FakeFeedback(), "guest_1")  # type: ignore[arg-type]
    assert ctx.genres == {"pop"}


async def test_over_1000_user_songs_all_read(reader: CatalogReader) -> None:
    fs = FakeFirestore(user_songs=[_song(f"spotify:{i}", "Band", f"Tune {i}") for i in range(2300)])
    ctx = await _ctx(reader, fs)
    assert len(ctx.known_songs) == 2300
    assert fs.song_queries == [(None, None)]  # one streamed query, no offset paging


async def test_quiz_seeded_songs_do_not_override_affinity(reader: CatalogReader) -> None:
    fs = FakeFirestore(
        decide_users=[
            {"id": "h", "user_id": UID, "quiz_artist_affinities": [{"artist_name": "Cher", "affinity": "occasionally"}]}
        ],
        user_songs=[_song(f"q{i}", "Cher", f"Tune {i}", source="quiz") for i in range(5)],
    )
    ctx = await _ctx(reader, fs)
    assert ctx.artists[artist_key("Cher")].weight == w.QUIZ_OCCASIONALLY
    assert len(ctx.known_songs) == 5


@pytest.mark.parametrize("source", ["manual", "known_songs", "enjoy_singing"])
def test_explicit_add_sources_are_manual_weight(source: str) -> None:
    assert w.song_weight({"source": source}) == w.SONG_MANUAL


async def test_hidden_keys_failure_degrades(reader: CatalogReader, caplog: pytest.LogCaptureFixture) -> None:
    class Boom:
        async def hidden_keys(self, user_id: str) -> set[str]:
            raise RuntimeError("down")

    ctx = await build_context(FakeFirestore(), reader, Boom(), UID)  # type: ignore[arg-type]
    assert ctx.hidden == set()
    assert any(r.levelname == "WARNING" for r in caplog.records)


# Found on prod 2026-10-22: Apple Music's ranked artists were ignored (not a top source), so a connected
# library changed nothing in the picks.
async def test_apple_music_artists_are_ranked_like_other_synced_services(reader: CatalogReader) -> None:
    fs = FakeFirestore(
        decide_users=[{"id": "h", "user_id": UID}],
        user_artists=[_artist("Toto", "apple_music", 1), _artist("Weezer", "apple_music", 100)],
        user_songs=[],
    )
    ctx = await _ctx(reader, fs)
    assert ctx.artists[artist_key("Toto")].weight == pytest.approx(w.artist_top(1))
    assert ctx.artists[artist_key("Toto")].source == "apple_music"
    assert ctx.artists[artist_key("Weezer")].weight == pytest.approx(w.artist_top(100))


def test_import_sources_weigh_like_their_counterparts() -> None:
    """Privacy-export plays are real listens (like Last.fm); playlist / screenshot picks like YT Music."""
    for source in ("spotify_export", "apple_export"):
        assert [w.song_weight({"source": source, "playcount": n}) for n in (12, 5, 1)] == [1.0, 0.7, 0.4]
    assert w.song_weight({"source": "spotify_playlist"}) == w.song_weight({"source": "screenshot"}) == 0.8


@pytest.mark.parametrize("source", ["spotify_export", "apple_export", "screenshot", "spotify_playlist"])
async def test_import_artists_are_ranked_top_artists(reader: CatalogReader, source: str) -> None:
    fs = FakeFirestore(
        decide_users=[{"id": "h", "user_id": UID}],
        user_artists=[_artist("Toto", source, 1)],
        user_songs=[],
    )
    ctx = await _ctx(reader, fs)
    assert ctx.artists[artist_key("Toto")].weight == pytest.approx(w.artist_top(1))
    assert ctx.artists[artist_key("Toto")].source == source


def _session(title: str, source: str = "voice_session", **extra: Any) -> dict[str, Any]:
    return _song(f"x:{title}", "Weezer", title, source=source, **extra)


async def test_context_session_yes_is_known(reader: CatalogReader) -> None:
    fs = FakeFirestore(
        user_songs=[
            _session("Yes Song", session_would_sing="yes", session_feel="not_for_me"),
            _session("Other Key", session_would_sing="yes_other_key"),
            _session("Fine Song", session_feel="fine"),
        ]
    )
    ctx = await _ctx(reader, fs)
    assert ctx.known_songs[song_key("Weezer", "Yes Song")] == w.SONG_SESSION_YES
    assert ctx.known_songs[song_key("Weezer", "Other Key")] == w.SONG_SESSION_YES
    assert ctx.known_songs[song_key("Weezer", "Fine Song")] == w.SONG_SESSION_FINE
    assert ctx.session_no == set()


async def test_session_no_penalised_not_known(reader: CatalogReader) -> None:
    fs = FakeFirestore(user_songs=[_session("No Song", session_would_sing="no", session_feel="fine")])
    ctx = await _ctx(reader, fs)
    k = song_key("Weezer", "No Song")
    assert k in ctx.session_no and k not in ctx.known_songs


async def test_session_labels_apply_whatever_the_row_source(reader: CatalogReader) -> None:
    """A session attempt keeps an existing row's source: promotion reads the session_* fields."""
    fs = FakeFirestore(
        user_songs=[
            _session("Yes Export", source="spotify_export", playcount=1, session_would_sing="yes"),
            _session("No Export", source="spotify_export", playcount=50, session_would_sing="no"),
            _session("Fine Loved", source="manual", loved=True, session_feel="fine"),
        ]
    )
    ctx = await _ctx(reader, fs)
    assert ctx.known_songs[song_key("Weezer", "Yes Export")] == w.SONG_SESSION_YES  # 0.4 by plays alone
    assert song_key("Weezer", "No Export") in ctx.session_no
    assert song_key("Weezer", "No Export") not in ctx.known_songs
    assert ctx.known_songs[song_key("Weezer", "Fine Loved")] == w.SONG_MANUAL  # max wins


def test_session_weights() -> None:
    assert (w.SONG_SESSION_YES, w.SONG_SESSION_FINE, w.SESSION_NO_PENALTY) == (1.0, 0.6, 0.15)


async def test_session_only_rows_are_not_taste(reader: CatalogReader) -> None:
    """Pool songs probe range, not taste: a voice_session-only row is known only through a session label
    and never makes its artist a known artist."""
    fs = FakeFirestore(
        user_songs=[
            _session("Meh Song", session_feel="not_for_me", sources=["voice_session"]),
            _session("No Song", session_would_sing="no", session_feel="fine"),
            _session("Yes Song", session_would_sing="yes", sources=["voice_session"]),
        ]
    )
    ctx = await _ctx(reader, fs)
    assert ctx.known_songs == {song_key("Weezer", "Yes Song"): w.SONG_SESSION_YES}
    assert ctx.known_without_karaoke == [(song_key("Weezer", "Yes Song"), "Weezer", "Yes Song")]
    assert artist_key("Weezer") not in ctx.artists
    assert w.song_weight({"source": "voice_session"}) == 0.0


async def test_session_row_with_another_source_still_signals_the_artist(reader: CatalogReader) -> None:
    fs = FakeFirestore(
        user_songs=[_session("Export", source="spotify_export", sources=["spotify_export", "voice_session"],
                             playcount=3, session_feel="not_for_me")]
    )  # fmt: skip
    ctx = await _ctx(reader, fs)
    assert ctx.known_songs == {song_key("Weezer", "Export"): w.SONG_LASTFM_MID}
    assert artist_key("Weezer") in ctx.artists


async def test_session_created_row_later_imported_weighs_by_its_best_source(reader: CatalogReader) -> None:
    """Imports keep a row's first ``source`` and only add to ``sources``: weigh the best of them."""
    fs = FakeFirestore(user_songs=[_session("Later Export", sources=["voice_session", "spotify_export"], playcount=12)])
    ctx = await _ctx(reader, fs)
    assert ctx.known_songs == {song_key("Weezer", "Later Export"): w.SONG_LASTFM_HIGH}
    assert artist_key("Weezer") in ctx.artists


async def test_loved_session_only_row_counts_toward_its_artist(reader: CatalogReader) -> None:
    """ "Love it" on a song first met in a voice session is a taste signal, the same as a manual love."""
    loved = FakeFirestore(
        user_songs=[_session("Loved Song", sources=["voice_session"], loved=True, session_feel="loved")]
    )
    manual = FakeFirestore(user_songs=[_song("x:Loved Song", "Weezer", "Loved Song", source="manual", loved=True)])
    from_session, from_manual = await _ctx(reader, loved), await _ctx(reader, manual)
    assert from_session.known_songs == {song_key("Weezer", "Loved Song"): w.SONG_MANUAL}
    assert from_session.artists[artist_key("Weezer")].weight == from_manual.artists[artist_key("Weezer")].weight
    unloved = FakeFirestore(user_songs=[_session("Loved Song", sources=["voice_session"], loved=False)])
    assert artist_key("Weezer") not in (await _ctx(reader, unloved)).artists


# ------------------------------------------------------------------ Phase 4b: sung history + surveys


def _sung(title: str, times: int, feel: str | None = None, source: str = "karaoke_log", **extra: Any) -> dict[str, Any]:
    return _song(f"k:{title}", "Weezer", title, source=source, times_sung=times, sung_feel_latest=feel, **extra)


def test_sung_weights() -> None:
    assert (w.SONG_SUNG_GREAT, w.SONG_SUNG_ONCE, w.SONG_SUNG_OK, w.SURVEY_NO_PENALTY) == (1.0, 0.8, 0.7, 0.4)
    assert w.sung_song_weight({"times_sung": 1, "sung_feel_latest": "great"}) == 1.0
    assert w.sung_song_weight({"times_sung": 2, "sung_feel_latest": "ok"}) == 1.0
    assert w.sung_song_weight({"times_sung": 2}) == 1.0
    assert w.sung_song_weight({"times_sung": 1}) == 0.8
    assert w.sung_song_weight({"times_sung": 1, "sung_feel_latest": "ok"}) == 0.7
    assert w.sung_song_weight({"times_sung": 5, "sung_feel_latest": "bad"}) is None
    assert w.sung_song_weight({"times_sung": 0, "sung_feel_latest": "great"}) is None
    assert w.sung_song_weight({}) is None


async def test_sung_great_is_known_at_full_weight(reader: CatalogReader) -> None:
    ctx = await _ctx(reader, FakeFirestore(user_songs=[_sung("Great Song", 1, "great")]))
    k = song_key("Weezer", "Great Song")
    assert ctx.known_songs[k] == w.SONG_SUNG_GREAT
    assert k in ctx.go_tos and k not in ctx.survey_no
    assert artist_key("Weezer") in ctx.artists


async def test_sung_once_no_survey_is_point_eight(reader: CatalogReader) -> None:
    fs = FakeFirestore(user_songs=[_sung("Once", 1), _sung("Ok Once", 1, "ok"), _sung("Twice", 2)])
    ctx = await _ctx(reader, fs)
    once, ok, twice = (song_key("Weezer", t) for t in ("Once", "Ok Once", "Twice"))
    assert ctx.known_songs[once] == w.SONG_SUNG_ONCE  # beats karaoke_log's default 0.6
    assert ctx.known_songs[ok] == w.SONG_SUNG_OK
    assert ctx.known_songs[twice] == w.SONG_SUNG_GREAT
    assert ctx.go_tos == {twice}  # once (no feel / ok) is not a go-to


async def test_sung_weight_is_maxed_with_base_and_session(reader: CatalogReader) -> None:
    fs = FakeFirestore(
        user_songs=[
            _sung("Loved Ok", 1, "ok", source="manual", loved=True),
            _sung("Export Once", 1, source="spotify_export", playcount=1),
        ]
    )
    ctx = await _ctx(reader, fs)
    assert ctx.known_songs[song_key("Weezer", "Loved Ok")] == w.SONG_MANUAL  # max(1.0, 0.7)
    assert ctx.known_songs[song_key("Weezer", "Export Once")] == w.SONG_SUNG_ONCE  # max(0.4, 0.8)


async def test_latest_bad_moves_to_survey_no_even_if_loved(reader: CatalogReader) -> None:
    fs = FakeFirestore(user_songs=[_sung("Bad Song", 4, "bad", source="manual", loved=True)])
    ctx = await _ctx(reader, fs)
    k = song_key("Weezer", "Bad Song")
    assert k in ctx.survey_no
    assert k not in ctx.known_songs and k not in ctx.go_tos
    assert ctx.known_without_karaoke == []
    assert artist_key("Weezer") not in ctx.artists  # its only song doesn't count toward the artist
    assert ctx.session_no == set()


async def test_go_tos_exclude_hidden(reader: CatalogReader) -> None:
    fs = FakeFirestore(user_songs=[_sung("Hidden Fave", 3, "great"), _sung("Fave", 3, "great")])
    ctx = await _ctx(reader, fs, hidden={song_key("Weezer", "Hidden Fave")})
    assert ctx.go_tos == {song_key("Weezer", "Fave")}


async def test_key_hints_from_latest_key_answer(reader: CatalogReader) -> None:
    fs = FakeFirestore(
        user_songs=[
            _sung("High", 1, sung_key_latest="too_high"),
            _sung("Low", 1, sung_key_latest="too_low"),
            _sung("Right", 1, sung_key_latest="right"),
            _sung("None", 1),
        ]
    )
    ctx = await _ctx(reader, fs)
    assert ctx.key_hints == {song_key("Weezer", "High"): -2, song_key("Weezer", "Low"): 2}


async def test_row_song_key_is_the_library_identity(reader: CatalogReader) -> None:
    """A karaoke_log row stores the resolved catalog key; the sung spelling must not split the song."""
    fs = FakeFirestore(user_songs=[_song("muse|starlight", "Muse", "Starlite (live)", source="karaoke_log",
                                         song_key="muse|starlight", times_sung=2)])  # fmt: skip
    ctx = await _ctx(reader, fs)
    assert ctx.go_tos == {"muse|starlight"}
    assert "muse|starlight" in ctx.known_songs and ctx.known_without_karaoke == []
