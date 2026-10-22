"""One write path for every music import (screenshot, Spotify playlist embed, Spotify / Apple
privacy exports, YouTube Music, ListenBrainz, Last.fm, Spotify).

``ingest`` matches tracks against the catalog and merges them into ``user_songs`` and the
source's top artists into ``user_artists``; ``record_import`` / ``get_import`` keep one status doc
per (user, source) in ``user_imports``; ``remove_source_data`` undoes a source.

Rows (shared with the karaoke log / "Love it", decide #168). A track with a resolved ``song_key``
lands on ``{user_id}:{kn_id}`` when catalog.db maps the key to a KaraokeNerds id and that row
exists, else on ``loved_doc_id(user_id, song_key)`` (``song_feedback_service.user_song_row_id``).
Without a key a catalog match keeps the legacy ``{user_id}:{song_id}`` row and an unmatched track
the synthetic ``{user_id}:{source}:{norm_artist}:{norm_title}`` row of ``SyncService``.

Merges are additive: an existing row keeps its ``source`` (the new one joins ``sources``) and
``times_sung``, ``last_sung_at``, ``loved`` and ``hidden`` are never written. ``playcount`` merges
as ``max(old, new)`` (re-importing the same export never doubles) or ``old + new`` (``add``:
incremental feeds such as the ListenBrainz poller). Timestamps are ISO strings.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from backend.services.firestore_service import FirestoreService
from backend.services.song_feedback_service import (
    USER_SONGS_COLLECTION,
    resolve_song_keys,
    user_song_row_id,
)
from backend.services.track_matcher import MatchedTrack, TrackMatcher
from karaoke_decide.song_search.text_normalize import identity_normalize

logger = logging.getLogger(__name__)

ImportSource = Literal[
    "screenshot",
    "spotify_playlist",
    "spotify_export",
    "apple_export",
    "youtube_music",
    "listenbrainz",
    "lastfm",
    "spotify",
    "apple_music",  # a music_services connection (decide v0.20); listed here so it can be summarised and removed
]
PlayMode = Literal["max", "add"]

USER_ARTISTS_COLLECTION = "user_artists"
EXPORT_ARTISTS_COLLECTION = "user_import_artists"
MAX_DERIVED_ARTISTS = 1000


@dataclass
class IngestTrack:
    artist: str
    title: str
    plays: int | None = None
    ms_played: int | None = None
    last_ts: int | None = None  # Unix seconds of the latest play


@dataclass
class IngestArtist:
    name: str
    plays: int | None = None
    rank: int | None = None


@dataclass
class IngestResult:
    artists_added: int
    songs_added: int
    songs_updated: int
    tracks_matched: int


class IngestReader(Protocol):
    """The catalog.db calls ingest makes (``CatalogReader`` satisfies it)."""

    def kn_song_keys(self, kn_ids: Sequence[int]) -> dict[int, str]: ...
    def has_karaoke_key(self, key: str) -> bool: ...
    def kn_ids_for_song_keys(self, song_keys: Sequence[str]) -> dict[str, int]: ...


@dataclass
class _Row:
    """One ``user_songs`` row an ingest writes (tracks resolving to the same row are summed)."""

    song_id: str
    song_key: str | None
    artist: str
    title: str
    has_karaoke: bool
    plays: int | None = None
    ms_played: int | None = None
    last_ts: int | None = None


def _default_reader() -> IngestReader | None:
    from backend.services.catalog_store import get_catalog_store

    return get_catalog_store().reader


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _iso(ts: int | None) -> str | None:
    return datetime.fromtimestamp(ts, UTC).isoformat() if ts is not None else None


def _later(a: Any, b: str | None) -> Any:
    if not a or not b:
        return a or b
    try:
        return b if datetime.fromisoformat(str(b)) > datetime.fromisoformat(str(a)) else a
    except ValueError:
        return b


def _add(a: int | None, b: int | None) -> int | None:
    return b if a is None else a if b is None else a + b


def _to_int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _merge_count(old: Any, new: int | None, mode: PlayMode) -> int | None:
    """The merged count, or None when there is nothing new to write."""
    if new is None:
        return None
    return _to_int(old) + new if mode == "add" else max(_to_int(old), new)


def _sources(doc: dict[str, Any]) -> list[str]:
    """The row's ``sources``; rows written before the field existed have just their ``source``."""
    sources = doc.get("sources")
    if isinstance(sources, list):
        return [str(s) for s in sources]
    return [doc["source"]] if doc.get("source") else []


def artist_doc_id(user_id: str, source: str, name: str) -> str:
    """``{user_id}:{source}:{safe_name}`` like ``SyncService._store_listenbrainz_artists``; a name
    with no a-z / 0-9 (any non-Latin script) gets a digest so artists never share a doc."""
    safe = re.sub(r"[^a-z0-9_]", "_", name.lower())[:50]
    if not re.search(r"[a-z0-9]", safe):
        safe = "h" + hashlib.sha1(identity_normalize(name).encode()).hexdigest()[:16]  # noqa: S324  (id)
    return f"{user_id}:{source}:{safe}"


class ImportIngestService:
    IMPORTS_COLLECTION = "user_imports"  # doc id f"{user_id}:{source}"

    def __init__(
        self,
        firestore: FirestoreService,
        track_matcher: TrackMatcher | None = None,
        reader: Callable[[], IngestReader | None] = _default_reader,
    ) -> None:
        """``reader`` returns the loaded catalog.db reader, or None while it warms up."""
        self.db = firestore
        self._track_matcher = track_matcher
        self._reader = reader

    @property
    def track_matcher(self) -> TrackMatcher:
        if self._track_matcher is None:
            from backend.services.track_matcher import get_track_matcher

            self._track_matcher = get_track_matcher()
        return self._track_matcher

    # ------------------------------------------------------------------ ingest

    async def ingest(
        self,
        user_id: str,
        source: ImportSource,
        tracks: list[IngestTrack],
        artists: list[IngestArtist] | None = None,
        play_mode: PlayMode = "max",
    ) -> IngestResult:
        tracks = [t for t in tracks if (t.artist or "").strip() and (t.title or "").strip()]
        matched = (
            await self.track_matcher.batch_match_tracks(
                [{"artist": t.artist, "title": t.title, "playcount": t.plays} for t in tracks]
            )
            if tracks
            else []
        )
        added, updated = await self._write_songs(user_id, source, tracks, matched, play_mode)
        if artists is None:
            artists = _derive_artists(tracks)
        artists_added = await self._write_artists(user_id, source, artists, play_mode)
        return IngestResult(
            artists_added=artists_added,
            songs_added=added,
            songs_updated=updated,
            tracks_matched=sum(1 for m in matched if m.catalog_song is not None),
        )

    def _resolve(
        self, tracks: list[IngestTrack], matched: list[MatchedTrack]
    ) -> tuple[list[str | None], dict[str, int]]:
        """``(song_key per track, {song_key: kn_id})``; nothing resolves while catalog.db warms up."""
        reader = self._reader()
        if reader is None:
            return [None] * len(tracks), {}
        try:
            items = [
                (t.artist, t.title, m.catalog_song.id if m.catalog_song is not None else None)
                for t, m in zip(tracks, matched, strict=True)
            ]
            keys = resolve_song_keys(reader, items)
            return keys, reader.kn_ids_for_song_keys([k for k in dict.fromkeys(keys) if k])
        except Exception:
            logger.warning("import song_key resolution failed; using legacy row ids", exc_info=True)
            return [None] * len(tracks), {}

    async def _write_songs(
        self,
        user_id: str,
        source: str,
        tracks: list[IngestTrack],
        matched: list[MatchedTrack],
        mode: PlayMode,
    ) -> tuple[int, int]:
        if not tracks:
            return 0, 0
        keys, kn_ids = await asyncio.to_thread(self._resolve, tracks, matched)  # SQLite: off the loop
        kn_rows = [f"{user_id}:{kn}" for kn in dict.fromkeys(kn_ids.values())]
        existing = await self.db.get_many(USER_SONGS_COLLECTION, kn_rows)

        rows: dict[str, _Row] = {}
        for track, match, key in zip(tracks, matched, keys, strict=True):
            song = match.catalog_song
            if key is not None:
                kn = kn_ids.get(key)
                row_id = user_song_row_id(user_id, key, kn, existing)
                song_id = str(kn) if row_id == f"{user_id}:{kn}" else key  # like the karaoke log's rows
            elif song is not None:
                song_id = str(song.id)
                row_id = f"{user_id}:{song_id}"
            else:
                song_id = f"{source}:{match.normalized_artist}:{match.normalized_title}"
                row_id = f"{user_id}:{song_id}"
            row = rows.get(row_id)
            if row is None:
                row = rows[row_id] = _Row(
                    song_id=song_id,
                    song_key=key,
                    artist=song.artist if song is not None else match.original_artist,
                    title=song.title if song is not None else match.original_title,
                    has_karaoke=song is not None or key is not None,
                )
            row.plays = _add(row.plays, track.plays)
            row.ms_played = _add(row.ms_played, track.ms_played)
            if track.last_ts is not None:
                row.last_ts = max(row.last_ts or 0, track.last_ts)

        missing = [r for r in rows if r not in existing]
        existing.update(await self.db.get_many(USER_SONGS_COLLECTION, missing))

        now = _now()
        docs: dict[str, dict[str, Any]] = {}
        for row_id, row in rows.items():
            cur = existing.get(row_id)
            docs[row_id] = (
                self._update_doc(cur, row, source, mode, now)
                if cur is not None
                else self._new_doc(user_id, row_id, row, source, now)
            )
        await self.db.batch_set(USER_SONGS_COLLECTION, docs, merge=True)
        updated = sum(1 for r in rows if r in existing)
        return len(rows) - updated, updated

    @staticmethod
    def _new_doc(user_id: str, row_id: str, row: _Row, source: str, now: str) -> dict[str, Any]:
        """``SyncService._upsert_user_songs``'s field set, plus ``sources`` / ``song_key`` / ``ms_played``."""
        doc: dict[str, Any] = {
            "id": row_id,
            "user_id": user_id,
            "song_id": row.song_id,
            "source": source,
            "sources": [source],
            "sync_count": 1,
            "playcount": row.plays,
            "ms_played": row.ms_played,
            "rank": None,
            "last_played_at": _iso(row.last_ts) or now,
            "is_saved": source == "spotify",
            "times_sung": 0,
            "last_sung_at": None,
            "average_rating": None,
            "notes": None,
            "artist": row.artist,
            "title": row.title,
            "has_karaoke_version": row.has_karaoke,
            "spotify_popularity": None,
            "duration_ms": None,
            "explicit": False,
            "created_at": now,
            "updated_at": now,
        }
        if row.song_key is not None:
            doc["song_key"] = row.song_key
        return doc

    @staticmethod
    def _update_doc(cur: dict[str, Any], row: _Row, source: str, mode: PlayMode, now: str) -> dict[str, Any]:
        """The fields merged into an existing row (never times_sung / last_sung_at / loved / hidden / source)."""
        sources = _sources(cur)
        patch: dict[str, Any] = {"sources": sources + [source] if source not in sources else sources, "updated_at": now}
        playcount = _merge_count(cur.get("playcount"), row.plays, mode)
        if playcount is not None:
            patch["playcount"] = playcount
        ms_played = _merge_count(cur.get("ms_played"), row.ms_played, mode)
        if ms_played is not None:
            patch["ms_played"] = ms_played
        if row.last_ts is not None:
            patch["last_played_at"] = _later(cur.get("last_played_at"), _iso(row.last_ts))
        if row.song_key is not None and not cur.get("song_key"):
            patch["song_key"] = row.song_key
        return patch

    async def _write_artists(self, user_id: str, source: str, artists: list[IngestArtist], mode: PlayMode) -> int:
        merged: dict[str, dict[str, Any]] = {}
        for i, a in enumerate(artists, 1):
            name = (a.name or "").strip()
            if not name:
                continue
            doc_id = artist_doc_id(user_id, source, name)
            rank = a.rank if a.rank is not None else i
            prev = merged.get(doc_id)
            if prev is not None:
                prev["playcount"] = _add(prev["playcount"], a.plays)
                prev["rank"] = min(prev["rank"], rank)
                continue
            merged[doc_id] = {
                "id": doc_id,
                "user_id": user_id,
                "source": source,
                "artist_name": name,
                "artist_key": identity_normalize(name),
                "playcount": a.plays,
                "rank": rank,
            }
        if not merged:
            return 0
        existing = await self.db.get_many(USER_ARTISTS_COLLECTION, list(merged))
        now = _now()
        for doc_id, doc in merged.items():
            if doc_id in existing:
                count = _merge_count(existing[doc_id].get("playcount"), doc["playcount"], mode)
                doc["playcount"] = count if count is not None else existing[doc_id].get("playcount")
            doc["updated_at"] = now
        await self.db.batch_set(USER_ARTISTS_COLLECTION, merged, merge=True)
        return len(merged)

    # ------------------------------------------------------------------ status docs

    async def record_import(
        self,
        user_id: str,
        source: ImportSource,
        *,
        status: str,
        result: IngestResult | None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        data: dict[str, Any] = {"user_id": user_id, "source": source, "status": status, "last_import_at": _now()}
        if result is not None:
            data["artists"] = result.artists_added
            data["songs"] = result.songs_added + result.songs_updated
        data.update(extra or {})
        await self.db.set_document(self.IMPORTS_COLLECTION, f"{user_id}:{source}", data, merge=True)

    async def list_imports(self, user_id: str) -> list[dict[str, Any]]:
        return await self.db.query_documents(self.IMPORTS_COLLECTION, filters=[("user_id", "==", user_id)])

    async def get_import(self, user_id: str, source: ImportSource) -> dict[str, Any] | None:
        return await self.db.get_document(self.IMPORTS_COLLECTION, f"{user_id}:{source}")

    # ------------------------------------------------------------------ removal

    async def remove_source_data(self, user_id: str, source: ImportSource) -> tuple[int, int]:
        """Undo ``source`` for ``user_id``: ``(song rows deleted, artist rows deleted)``.

        A row whose ``source`` is this one is deleted unless something else holds it: another
        source in ``sources`` takes over as ``source``; a loved row becomes ``manual`` and a sung
        one ``karaoke_log`` (their imported play counts cleared). Every other row just leaves
        this source out of ``sources``."""
        rows = await self.db.query_documents(USER_SONGS_COLLECTION, filters=[("user_id", "==", user_id)])
        deletes: list[str] = []
        patches: dict[str, dict[str, Any]] = {}
        now = _now()
        for row in rows:
            sources = _sources(row)
            if source not in sources and row.get("source") != source:
                continue
            rest = [s for s in sources if s != source]
            patch: dict[str, Any] = {"sources": rest, "updated_at": now}
            if row.get("source") == source:
                if rest:
                    patch["source"] = rest[0]
                elif row.get("loved") is True or _to_int(row.get("times_sung")) > 0:
                    patch.update(
                        source="manual" if row.get("loved") is True else "karaoke_log",
                        playcount=None,
                        ms_played=None,
                    )
                else:
                    deletes.append(row["id"])
                    continue
            patches[row["id"]] = patch
        if patches:
            await self.db.batch_set(USER_SONGS_COLLECTION, patches, merge=True)
        if deletes:
            await self.db.batch_delete(USER_SONGS_COLLECTION, deletes)

        artist_docs = await self.db.query_documents(USER_ARTISTS_COLLECTION, filters=[("user_id", "==", user_id)])
        artist_ids = [d["id"] for d in artist_docs if d.get("source") == source]
        if artist_ids:
            await self.db.batch_delete(USER_ARTISTS_COLLECTION, artist_ids)
        # Export imports keep running artist sums per export; they go with the source.
        sums = await self.db.query_documents(
            EXPORT_ARTISTS_COLLECTION, filters=[("user_id", "==", user_id), ("source", "==", source)]
        )
        if sums:
            await self.db.batch_delete(EXPORT_ARTISTS_COLLECTION, [d["id"] for d in sums])
        await self.db.delete_document(self.IMPORTS_COLLECTION, f"{user_id}:{source}")
        return len(deletes), len(artist_ids)


def _derive_artists(tracks: list[IngestTrack]) -> list[IngestArtist]:
    """Top artists from tracks: plays summed per ``identity_normalize(artist)`` (a track with no
    play count counts once for ranking), top ``MAX_DERIVED_ARTISTS`` ranked."""
    names: dict[str, str] = {}
    weight: dict[str, int] = {}
    plays: dict[str, int | None] = {}
    for t in tracks:
        key = identity_normalize(t.artist)
        if not key:
            continue
        names.setdefault(key, t.artist.strip())
        weight[key] = weight.get(key, 0) + (t.plays if t.plays is not None else 1)
        plays[key] = _add(plays.get(key), t.plays)
    ranked = sorted(weight, key=lambda k: (-weight[k], k))[:MAX_DERIVED_ARTISTS]
    return [IngestArtist(name=names[k], plays=plays[k], rank=i) for i, k in enumerate(ranked, 1)]
