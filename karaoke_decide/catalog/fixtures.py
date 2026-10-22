"""Deterministic fixture catalog.db for tests and local dev (invented facets; no real lyrics).

python -m karaoke_decide.catalog.fixtures build <dir>   # writes <dir>/catalog.db
"""

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

from karaoke_decide.catalog.build import (
    ArtistRow,
    CatalogRows,
    KnRow,
    SimilarRow,
    SongRow,
    build_catalog_db,
    with_vocal_range,
)
from karaoke_decide.catalog.genres import GENRE_IDS
from karaoke_decide.catalog.keys import artist_key, match_key, song_key
from karaoke_decide.catalog.moods import mood_for
from karaoke_decide.vocal_range import tsv as vocal_range_tsv

_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
KN_FIXTURE = _FIXTURES / "song_search" / "kn_full_fixture.json"
# Invented vocal ranges (not measurements); shared with the offline fixture index.
VOCAL_RANGE_SONGS = _FIXTURES / "vocal_range" / "song_vocal_range_fixture.tsv"
VOCAL_RANGE_ARTISTS = _FIXTURES / "vocal_range" / "artist_vocal_range_fixture.tsv"

N_SYNTHETIC_ARTISTS = 20
SONGS_PER_SYNTHETIC_ARTIST = 10

_GENRES = GENRE_IDS[:-1]  # "other" is not a filterable facet
_ENERGY_BY_BAND = {"low": 0.25, "mid": 0.55, "high": 0.85}
_VALENCE = (0.15, 0.35, 0.65, 0.85)
_DURATIONS = (150_000, 240_000, 330_000)
_GENDERS = ("male", "female", "mixed")
_DECADES = tuple(range(1950, 2030, 10))

# Pinned rows that flows rely on: song_key -> overrides.
_PINS: dict[str, dict] = {
    "muse|starlight": {"genres": ("rock", "indie"), "decade": 2000, "energy": 0.85, "valence": 0.35, "gender": "male"},
    "adele|hello": {"genres": ("pop",), "decade": 2010, "energy": 0.3, "valence": 0.65, "gender": "female"},
}


def _digest(key: str) -> bytes:
    return hashlib.sha256(key.encode()).digest()


def _song(artist: str, title: str, brand_count: int, in_kn: int) -> SongRow:
    key = song_key(artist, title)
    h = _digest(key)
    pin = _PINS.get(key, {})
    genres = pin.get("genres") or tuple(dict.fromkeys((_GENRES[h[0] % len(_GENRES)], _GENRES[h[1] % len(_GENRES)])))
    decade = pin.get("decade", _DECADES[h[2] % len(_DECADES)])
    energy = pin.get("energy", _ENERGY_BY_BAND[("low", "mid", "high")[h[3] % 3]] + (h[4] % 10) / 100 - 0.05)
    valence = pin.get("valence", _VALENCE[h[5] % len(_VALENCE)])
    return SongRow(
        song_key=key,
        artist=artist,
        title=title,
        artist_key=artist_key(artist),
        brand_count=brand_count,
        is_classic=h[6] % 2,
        explicit=h[7] % 2,
        duration_ms=_DURATIONS[h[8] % 3] + (h[9] % 20) * 1000,
        year=decade + h[10] % 10,
        decade=f"{decade}s",
        lb_listeners=100 + int.from_bytes(h[11:14], "big") % 500_000,
        spotify_pop=h[14] % 101,
        energy=round(energy, 2),
        valence=valence,
        danceability=round(0.2 + (h[15] % 70) / 100, 2),
        tempo=float(70 + h[16] % 100),
        mood=mood_for(valence, energy),
        lead_gender=pin.get("gender", _GENDERS[h[17] % 3]),
        genres=genres,
        in_kn=in_kn,
    )


def _artists_and_similar(songs: list[SongRow]) -> tuple[list[ArtistRow], list[SimilarRow]]:
    by_artist: dict[str, list[SongRow]] = {}
    for s in songs:
        by_artist.setdefault(s.artist_key, []).append(s)
    artists: list[ArtistRow] = []
    primary: dict[str, str] = {}
    for key in sorted(by_artist):
        group = by_artist[key]
        counts = Counter(s.genres[0] for s in group)
        top = max(counts.values())
        main = next(g for g in _GENRES if counts.get(g) == top)
        primary[key] = main
        h = _digest(key)
        artists.append(
            ArtistRow(
                artist_key=key,
                name=group[0].artist,
                lb_listeners=sum(s.lb_listeners or 0 for s in group),
                popularity=h[0] % 101,
                gender=_GENDERS[h[1] % 3],
                genres=(main,),
            )
        )
    similar: list[SimilarRow] = []
    for genre in _GENRES:
        members = [k for k in sorted(primary) if primary[k] == genre]
        for i, key in enumerate(members):
            for step in (1, 2):
                other = members[(i + step) % len(members)]
                if other == key:
                    continue
                score = 0.5 + (_digest(f"{key}>{other}")[0] % 41) / 100
                similar.append(SimilarRow(key, other, round(score, 2), "fixture"))
                similar.append(SimilarRow(other, key, round(score, 2), "fixture"))
    return artists, similar


def _rows() -> CatalogRows:
    songs: list[SongRow] = []
    kn: list[KnRow] = []
    for kn_id, entry in enumerate(json.loads(KN_FIXTURE.read_text()), start=1):
        brands = [b.strip() for b in entry["Brands"].split(",") if b.strip()]
        songs.append(_song(entry["Artist"], entry["Title"], len(set(brands)), 1))
        kn.append(
            KnRow(
                kn_id=kn_id,
                song_key=song_key(entry["Artist"], entry["Title"]),
                artist=entry["Artist"],
                title=entry["Title"],
                brands=",".join(brands),
                brand_count=len(set(brands)),
                match_key=match_key(entry["Artist"], entry["Title"]),
            )
        )
    for a in range(1, N_SYNTHETIC_ARTISTS + 1):
        for n in range(1, SONGS_PER_SYNTHETIC_ARTIST + 1):
            songs.append(_song(f"Fixture Artist {a:02d}", f"Fixture Song {a:02d}-{n:02d}", 0, 0))
    artists, similar = _artists_and_similar(songs)
    genre_map = {"alternative rock": "indie", "classic rock": "classic-rock", "dance pop": "pop"}
    rows = CatalogRows(songs=songs, artists=artists, similar=similar, kn=kn, genre_map=genre_map)
    return with_vocal_range(rows, vocal_range_tsv.load(VOCAL_RANGE_SONGS, VOCAL_RANGE_ARTISTS, run="fixture"))


def build(dest: Path) -> Path:
    """Build ``<dest>/catalog.db`` from the KaraokeNerds fixture plus synthetic songs."""
    out = Path(dest) / "catalog.db"
    build_catalog_db(_rows(), out, run="fixture")
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] != "build":
        print("usage: python -m karaoke_decide.catalog.fixtures build <dir>", file=sys.stderr)
        return 2
    print(build(Path(argv[2])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
