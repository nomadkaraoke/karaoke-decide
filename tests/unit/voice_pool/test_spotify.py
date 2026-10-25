"""Shared Spotify-track lookup for voice-pool songs (fixture catalog.db + fake BigQuery; no GCP)."""

from __future__ import annotations

from karaoke_decide.voice_pool import spotify


def test_all_kn_ids_for_song_keys_lists_every_row(reader):
    ids = reader.all_kn_ids_for_song_keys(["seal|kiss from a rose", "adele|hello", "nobody|nothing"])
    assert len(ids["seal|kiss from a rose"]) == 2 and ids["seal|kiss from a rose"] == sorted(
        ids["seal|kiss from a rose"]
    )
    assert len(ids["adele|hello"]) == 1 and "nobody|nothing" not in ids


def test_resolve_song_keys_one_query_most_popular_track(reader, fake_bq):
    seal = reader.all_kn_ids_for_song_keys(["seal|kiss from a rose"])["seal|kiss from a rose"]
    best = max(seal, key=lambda kn: kn % 100)  # FakeBq: spotify_pop = kn % 100
    bq = fake_bq(durations={best: 287_000})
    out = spotify.resolve_song_keys(reader, bq, ["seal|kiss from a rose", "adele|hello", "nobody|nothing"])
    assert bq.sqls == [spotify.SPOTIFY_FOR_KN_SQL]
    assert out["seal|kiss from a rose"] == (f"sp{best:06d}", 287_000)
    assert out["adele|hello"][0].startswith("sp") and "nobody|nothing" not in out
    assert bq.configs[0].maximum_bytes_billed  # byte cap applies


def test_resolve_song_keys_without_kn_ids_makes_no_query(reader, fake_bq):
    bq = fake_bq()
    assert spotify.resolve_song_keys(reader, bq, ["nobody|nothing"]) == {}
    assert bq.sqls == []


def test_song_without_linked_track_is_absent(reader, fake_bq):
    (kn,) = reader.all_kn_ids_for_song_keys(["adele|hello"])["adele|hello"]
    assert spotify.resolve_song_keys(reader, fake_bq(missing={kn}), ["adele|hello"]) == {}
