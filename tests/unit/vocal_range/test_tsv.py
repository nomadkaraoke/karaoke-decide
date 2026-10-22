"""The vocal-range TSV reader shared by catalog.db and offline.db."""

import dataclasses
import gzip
from pathlib import Path

from karaoke_decide.etl import vocal_range_refresh as vrr
from karaoke_decide.vocal_range import tsv
from karaoke_decide.vocal_range.fit import SongVocalRange, UserVoiceRange, fit_song
from karaoke_decide.vocal_range.merge import ArtistRange, MergedSong

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "vocal_range"
SONGS = FIXTURES / "song_vocal_range_fixture.tsv"
ARTISTS = FIXTURES / "artist_vocal_range_fixture.tsv"
BARITONE = UserVoiceRange(low=45, high=69, comfort_low=50, comfort_high=64)


def test_fixture_columns_match_the_published_tsvs() -> None:
    assert SONGS.read_text().splitlines()[0].split("\t") == [f.name for f in dataclasses.fields(MergedSong)]
    assert ARTISTS.read_text().splitlines()[0].split("\t") == [f.name for f in dataclasses.fields(ArtistRange)]


def test_load_reads_plain_and_gz(tmp_path: Path) -> None:
    plain = tsv.load(SONGS, ARTISTS)
    gz_s, gz_a = tmp_path / "s.tsv.gz", tmp_path / "a.tsv.gz"
    gz_s.write_bytes(gzip.compress(SONGS.read_bytes()))
    gz_a.write_bytes(gzip.compress(ARTISTS.read_bytes()))
    assert tsv.load(gz_s, gz_a) == plain
    assert set(plain.songs) == {"muse|starlight", "muse|uprising", "adele|hello", "brandy and monica|the boy is mine"}
    assert set(plain.artists) == {"muse", "adele", "taylor swift"}


def test_octave_confidence_never_takes_gemini_confidence() -> None:
    r = tsv.load(SONGS, ARTISTS)
    assert r.songs["adele|hello"].octave_confidence is None
    assert r.songs["muse|uprising"].octave_confidence == 0.92
    assert r.songs["brandy and monica|the boy is mine"].is_duet is True
    assert r.songs["muse|starlight"].is_duet is False


def test_lookup_song_then_artist_rough_then_none() -> None:
    r = tsv.load(SONGS, ARTISTS)
    assert r.lookup("muse|uprising", "muse").tier == "notes"  # type: ignore[union-attr]
    rough = r.lookup("taylor swift|shake it off", "taylor swift")
    assert rough is not None and rough.tier == "rough" and rough.octave_confidence is None and rough.is_duet is None
    assert (rough.low, rough.high, rough.p10, rough.p50, rough.p90) == (57.0, 76.0, 60.0, 66.0, 72.0)
    assert r.lookup("lady gaga|shallow", "lady gaga") is None


def test_records_with_missing_numbers_are_dropped() -> None:
    rec = {"song_key": "x|y", "low_midi": "", "high_midi": "60", "p10": "1", "p50": "2", "p90": "3", "tier": "notes"}
    assert tsv.song_range(rec) is None
    assert tsv.artist_range({"artist_key": "x", "low_midi": None}) is None


def test_fixture_fits_for_a_baritone() -> None:
    """The fit each fixture song is meant to show for a baritone preset (45-69, comfort 50-64)."""
    r = tsv.load(SONGS, ARTISTS)

    def fit(key: str, artist: str) -> tuple[str, int, int]:
        s = r.lookup(key, artist)
        assert s is not None
        res = fit_song(BARITONE, SongVocalRange(s.low, s.high, s.p10, s.p50, s.p90, s.tier, s.octave_confidence))  # type: ignore[arg-type]
        return res.bucket, res.transpose, res.octave

    assert fit("muse|starlight", "muse") == ("fits", 0, 0)
    assert fit("muse|uprising", "muse") == ("fitsIn", -2, 0)
    assert fit("adele|hello", "adele")[0] == "fitsIn" and fit("adele|hello", "adele")[2] == -12  # soprano: octave down
    assert r.songs["adele|hello"].p10 >= 60 and r.songs["adele|hello"].p90 <= 84


def test_constants_match_the_publisher() -> None:
    assert tsv.TIER_CODES == {"measured": 4, "notes": 3, "estimated": 2, "rough": 1}
    assert vrr.SONG_TSV_BLOB == tsv.SONG_TSV_BLOB
    assert vrr.ARTIST_TSV_BLOB == tsv.ARTIST_TSV_BLOB
    assert vrr.POINTER_BLOB == tsv.POINTER_BLOB
    assert vrr.BUCKET == tsv.BUCKET
