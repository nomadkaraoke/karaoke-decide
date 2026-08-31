"""Spotify audio-features lookup — the mandatory metadata gate.

We only ever propose tracks we can fully characterize, so a candidate must match
a Spotify track that has audio features (instrumentalness, duration, etc.). This
service batch-matches (artist, title) pairs against the full ``spotify_tracks``
table joined to ``spotify_audio_features`` (229M rows), by normalized name with
variant handling.

Cost note: the full-table join is a ~30GB scan regardless of batch size, so we
do ONE batched query per run for all uncached tracks and cache each result
forever (audio features never change). Repeat runs are free.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from google.cloud import bigquery

from karaoke_decide.candidates.matching import (
    artist_variants,
    norm,
    strip_decorations,
    title_variants,
)

_ALNUM = re.compile(r"[^a-z0-9]+")


def _alnum(text: str) -> str:
    """Match BigQuery's REGEXP_REPLACE(lower(x), '[^a-z0-9]+', '')."""
    return _ALNUM.sub("", text.lower())


@dataclass(frozen=True)
class SpotifyFeatures:
    instrumentalness: float
    speechiness: float
    energy: float
    valence: float
    danceability: float
    tempo: float
    duration_ms: int
    popularity: int
    explicit: bool

    @property
    def duration_min(self) -> float:
        return self.duration_ms / 60000 if self.duration_ms else 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpotifyFeatures:
        """Rebuild from a cached ``as_dict()`` payload (ignores derived keys)."""
        return cls(
            instrumentalness=float(data["instrumentalness"]),
            speechiness=float(data["speechiness"]),
            energy=float(data["energy"]),
            valence=float(data["valence"]),
            danceability=float(data["danceability"]),
            tempo=float(data["tempo"]),
            duration_ms=int(data["duration_ms"]),
            popularity=int(data["popularity"]),
            explicit=bool(data["explicit"]),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "instrumentalness": round(self.instrumentalness, 3),
            "speechiness": round(self.speechiness, 3),
            "energy": round(self.energy, 3),
            "valence": round(self.valence, 3),
            "danceability": round(self.danceability, 3),
            "tempo": round(self.tempo, 1),
            "duration_ms": self.duration_ms,
            "duration_min": round(self.duration_min, 2),
            "popularity": self.popularity,
            "explicit": self.explicit,
        }


def _expected_keys(artist: str, title: str) -> list[tuple[str, str]]:
    """Candidate (alnum-artist, alnum-title) pairs to match a track by."""
    titles = {strip_decorations(title), title}
    a_keys = {_alnum(a) for a in artist_variants(artist)}
    t_keys = {_alnum(norm(t)) for t in titles} | {_alnum(t) for t in title_variants(title)}
    return [(a, t) for a in a_keys for t in t_keys if a and t]


class SpotifyFeaturesService:
    """Batch (artist, title) -> SpotifyFeatures via BigQuery."""

    PROJECT_ID = "nomadkaraoke"
    DATASET_ID = "karaoke_decide"

    def __init__(self, client: bigquery.Client | None = None):
        self._client = client

    @property
    def client(self) -> bigquery.Client:
        if self._client is None:
            self._client = bigquery.Client(project=self.PROJECT_ID)
        return self._client

    def lookup(self, tracks: list[tuple[str, str]]) -> dict[tuple[str, str], SpotifyFeatures]:
        """Return {(artist, title): SpotifyFeatures} for tracks that matched.

        ``tracks`` is the list of (artist, title) to look up in one query. The
        returned dict is keyed by the SAME (artist, title) tuples passed in;
        unmatched tracks are simply absent.
        """
        if not tracks:
            return {}

        # Collect all alnum artist/title keys across the batch for one scan.
        arts: set[str] = set()
        tits: set[str] = set()
        per_track_keys: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for artist, title in tracks:
            keys = _expected_keys(artist, title)
            per_track_keys[(artist, title)] = keys
            for a, t in keys:
                arts.add(a)
                tits.add(t)

        # Pick ONE real row per (artist, title) key — the most popular — rather
        # than MAX()-ing each column independently, which would frankenstein a
        # live version's duration with a studio version's instrumentalness.
        sql = f"""
        SELECT
          REGEXP_REPLACE(LOWER(t.artist_name), r'[^a-z0-9]+', '') AS na,
          REGEXP_REPLACE(LOWER(t.title),       r'[^a-z0-9]+', '') AS nt,
          ARRAY_AGG(
            STRUCT(
              af.instrumentalness AS instrumentalness,
              af.speechiness AS speechiness,
              af.energy AS energy,
              af.valence AS valence,
              af.danceability AS danceability,
              af.tempo AS tempo,
              t.duration_ms AS duration_ms,
              t.popularity AS popularity,
              CAST(t.explicit AS INT64) AS explicit
            )
            ORDER BY t.popularity DESC LIMIT 1
          )[OFFSET(0)] AS f
        FROM `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_tracks` t
        JOIN `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_audio_features` af
          ON af.track_id = t.spotify_id
        WHERE REGEXP_REPLACE(LOWER(t.artist_name), r'[^a-z0-9]+', '') IN UNNEST(@arts)
          AND REGEXP_REPLACE(LOWER(t.title),       r'[^a-z0-9]+', '') IN UNNEST(@tits)
        GROUP BY na, nt
        """
        job = self.client.query(
            sql,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ArrayQueryParameter("arts", "STRING", sorted(arts)),
                    bigquery.ArrayQueryParameter("tits", "STRING", sorted(tits)),
                ]
            ),
        )
        found: dict[tuple[str, str], SpotifyFeatures] = {}
        for row in job.result():
            f = row["f"]
            found[(row["na"], row["nt"])] = SpotifyFeatures(
                instrumentalness=float(f["instrumentalness"] or 0.0),
                speechiness=float(f["speechiness"] or 0.0),
                energy=float(f["energy"] or 0.0),
                valence=float(f["valence"] or 0.0),
                danceability=float(f["danceability"] or 0.0),
                tempo=float(f["tempo"] or 0.0),
                duration_ms=int(f["duration_ms"] or 0),
                popularity=int(f["popularity"] or 0),
                explicit=bool(f["explicit"]),
            )

        result: dict[tuple[str, str], SpotifyFeatures] = {}
        for track, keys in per_track_keys.items():
            for key in keys:
                if key in found:
                    result[track] = found[key]
                    break
        return result
