"""Deterministic fixture voice.db for the local stack and Maestro (invented ranges; no real data).

python -m karaoke_decide.voice_pool.fixtures build <dir>   # writes <dir>/voice.db

48 songs of the 3a fixture catalog (KaraokeNerds fixture songs), eight per voice-type centre
(bass ... soprano, ``vocal_range.notes.VOICE_TYPES``): male-led songs fill the three lower types,
female-led the three upper ones. Muse "Starlight" sits at the baritone centre. Every song has one
fake, embeddable version ``FIXTURE<n>`` and invented markers.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from karaoke_decide.catalog import fixtures as catalog_fixtures
from karaoke_decide.catalog.build import SongRow
from karaoke_decide.catalog.keys import song_key
from karaoke_decide.vocal_range.notes import VOICE_TYPES
from karaoke_decide.voice_pool.models import Version, pool_id
from karaoke_decide.voice_pool.voicedb import ANALYSIS_VERSION, build_voice_db

PER_TYPE = 8
STARLIGHT = song_key("Muse", "Starlight")
FIXTURE_TIME = datetime(2026, 10, 22, tzinfo=UTC)
_LOW_TYPES = ("bass", "baritone", "tenor")
_HIGH_TYPES = ("alto", "mezzo", "soprano")


def _assign(songs: list[SongRow]) -> list[tuple[SongRow, str]]:
    """(song, voice type): Starlight -> baritone, then male songs to the low types and female
    songs to the high ones (mixed songs fill whatever is short), PER_TYPE each."""
    by_key = {s.song_key: s for s in songs}
    picked: list[tuple[SongRow, str]] = [(by_key[STARLIGHT], "baritone")]
    used = {STARLIGHT}
    for types, genders in ((_LOW_TYPES, ("male", "mixed")), (_HIGH_TYPES, ("female", "mixed"))):
        for t in types:
            need = PER_TYPE - sum(1 for _, pt in picked if pt == t)
            for g in genders:
                for s in songs:
                    if need == 0:
                        break
                    if s.song_key not in used and s.lead_gender == g:
                        picked.append((s, t))
                        used.add(s.song_key)
                        need -= 1
    return picked


def _analysis(key: str, centre: float, n: int) -> dict[str, Any]:
    p50 = centre + (n % 3 - 1) * 0.5
    above = {str(m): round(max(0.0, (p50 + 9 - m) * 2.5), 1) for m in (64, 67, 69, 72, 76)}
    below = {str(m): round(max(0.0, (m - p50 + 9) * 2.5), 1) for m in (45, 48, 52)}
    return {
        "song_key": key,
        "pool_id": pool_id(key),
        "analysis_version": ANALYSIS_VERSION,
        "low": p50 - 7.0,
        "high": p50 + 8.0,
        "p10": p50 - 4.5,
        "p50": p50,
        "p90": p50 + 5.0,
        "voiced_s": 90.0 + n,
        "secs_above": json.dumps(above),
        "secs_below": json.dumps(below),
        "octave_split_male": n % 11 == 0,
        "octave_split_female": n % 13 == 0,
        "section_jump": [None, 1.5, 3.0][n % 3],
        "sustained_high_count": n % 5,
        "sustained_high_s": float(n % 5) * 1.2,
        "longest_held_s": 1.0 + (n % 4) * 0.5,
        "high_loudness_db": 2.0 + (n % 3),
        "syllables_per_s": 1.5 + (n % 6) * 0.4,
        "density_source": "onsets",
        "longest_phrase_s": 5.0 + n % 7,
        "intro_s": 4.0 + (n % 5) * 3,
        "key": n % 12,
        "mode": n % 2,
        "key_changes": int(n % 9 == 0),
        "energy": None,  # filled from the catalog row
        "valence": None,
    }


def _songs() -> list[SongRow]:
    return sorted((s for s in catalog_fixtures._rows().songs if s.in_kn), key=lambda s: s.song_key)


def pooled_song_keys() -> set[str]:
    """The song keys ``build`` puts in the fixture voice.db."""
    return {s.song_key for s, _ in _assign(_songs())}


def build(dest: Path) -> Path:
    """Write ``<dest>/voice.db`` (see module doc) and return its path."""
    songs = _songs()
    centres = {t: (lo + hi) / 2 for t, (lo, hi) in VOICE_TYPES.items()}
    pool: list[dict[str, Any]] = []
    analysis: list[dict[str, Any]] = []
    for n, (s, vtype) in enumerate(_assign(songs), start=1):
        pool.append(
            {
                "song_key": s.song_key,
                "pool_id": pool_id(s.song_key),
                "artist": s.artist,
                "title": s.title,
                "artist_key": s.artist_key,
                "source": "shared",
                "stratum": f"fixture|{vtype}",
                "spotify_id": None,
                "duration_ms": s.duration_ms,
                "popularity": s.spotify_pop,
                "lead_gender": s.lead_gender,
                "versions": (Version(f"FIXTURE{n}", "NOMAD", "Nomad Karaoke", 0, True),),
            }
        )
        analysis.append({**_analysis(s.song_key, centres[vtype], n), "energy": s.energy, "valence": s.valence})
    out = Path(dest) / "voice.db"
    build_voice_db(pool, analysis, {}, out, "fixture", now=FIXTURE_TIME)
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] != "build":
        print("usage: python -m karaoke_decide.voice_pool.fixtures build <dir>", file=sys.stderr)
        return 2
    print(build(Path(argv[2])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
