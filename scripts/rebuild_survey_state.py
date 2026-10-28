#!/usr/bin/env python3
"""One-off (ruling R18): rebuild per-song survey state and the crowd counters from the karaoke log.

Karaoke-log records written before Phase 4b carry survey answers, but their ``user_songs`` rows have
no ``sung_surveys`` map and they never voted in ``song_crowd_stats``. This script backfills both,
through the exact Phase 4b code paths in ``backend.services.karaoke_log_service`` (``_survey_entry``,
``_derive_survey_fields``, ``_crowd_vote``, ``_crowd_pleaser``) and ``backend.services.crowd_stats``
(``apply_vote``, ``apply_pleaser``, ``crowd_doc_id``, ``voice_bucket``), never a reimplementation:

1. Every ``karaoke_log`` record that counts on a ``user_songs`` row (its ``counted`` marker, the row
   the live transaction keeps the survey on) gives that row one ``sung_surveys`` entry, built by
   ``_survey_entry`` with the row's existing entry for the record as ``prev``. So per-field
   ``at`` stamps and buckets the live path already wrote are kept, and an entry that is new gets the
   singer's current voice bucket (as the live path does when a key answer is set). The row's derived
   ``sung_*`` fields and ``crowd_bucket`` are then recomputed by ``_derive_survey_fields``. Entries for
   records that no longer count there are dropped. A row is written only when a field differs.
2. ``song_crowd_stats`` is rebuilt FROM SCRATCH: one vote per rebuilt row (its ``_crowd_vote`` and
   ``_crowd_pleaser``) added to empty stats with ``apply_vote`` / ``apply_pleaser``. A doc is written
   only when its counts differ, and docs no rebuilt row votes for are deleted. Votes are never added on
   top of what is there, so running it twice gives the same result (the second run changes nothing).

Unresolved songs (``user_karaoke_unresolved``) carry no survey state and no votes, as in the live path.
By design, a from-scratch rebuild drops votes of singers whose accounts were deleted (the live path
keeps them); run right after the 4b deploy there are none.

Not transactional against live writes: run it in a quiet window (not during a Nomad night). A survey
written while it runs may be overwritten on its row; re-running converges (it rebuilds from the log).

Usage (from the repo root, defaults to a dry run that writes nothing):

    # 1. dry run: read-only, prints what would change (the read-only ADC is enough)
    poetry run python scripts/rebuild_survey_state.py

    # 2. apply: needs Firestore write access (from the workspace root: scripts/gcp-admin ...)
    poetry run python scripts/rebuild_survey_state.py --apply

``--project`` (default ``nomadkaraoke``) and ``--database`` (default ``(default)``) pick the Firestore.
Logs at INFO / WARNING only.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.crowd_stats import (  # noqa: E402
    CROWD_COLLECTION,
    KEY_ANSWERS,
    VOICE_BUCKETS,
    apply_pleaser,
    apply_vote,
    crowd_doc_id,
    voice_bucket,
)
from backend.services.karaoke_log_service import (  # noqa: E402
    LOG_COLLECTION,
    _counts,
    _crowd_pleaser,
    _crowd_vote,
    _derive_survey_fields,
    _key_is_new,
    _survey_entry,
    _surveys,
)
from backend.services.song_feedback_service import USER_SONGS_COLLECTION  # noqa: E402
from backend.services.voice_profile_service import FIELD as VOICE_PROFILE_FIELD  # noqa: E402
from backend.services.voice_profile_service import find_user_doc  # noqa: E402

logger = logging.getLogger("rebuild_survey_state")

# The row fields _derive_survey_fields owns.
DERIVED_FIELDS = (
    "sung_surveys",
    "sung_feel_counts",
    "sung_feel_latest",
    "sung_key_latest",
    "sung_crowd_pleaser_count",
    "crowd_bucket",
)


class RebuildFirestore(Protocol):
    async def get_document(self, collection: str, doc_id: str) -> dict[str, Any] | None: ...
    async def query_documents(
        self, collection: str, filters: list[tuple[str, str, Any]] | None = None, *, limit: int | None = None
    ) -> list[dict[str, Any]]: ...
    async def update_document(self, collection: str, doc_id: str, data: dict[str, Any]) -> None: ...
    async def set_document(self, collection: str, doc_id: str, data: dict[str, Any], merge: bool = False) -> None: ...
    async def delete_document(self, collection: str, doc_id: str) -> None: ...


@dataclass
class Summary:
    apply: bool
    log_records: int = 0
    counted_records: int = 0
    rows: int = 0
    rows_missing: int = 0
    rows_changed: int = 0
    entries_added: int = 0
    entries_removed: int = 0
    bucket_lookups: int = 0
    crowd_songs: int = 0
    crowd_written: int = 0
    crowd_unchanged: int = 0
    crowd_deleted: int = 0
    changed_row_ids: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        mode = "APPLIED" if self.apply else "DRY RUN (nothing written; pass --apply to write)"
        verb = "" if self.apply else "would be "
        return [
            f"rebuild_survey_state: {mode}",
            f"  karaoke_log records scanned: {self.log_records} ({self.counted_records} counting on a user_songs row)",
            f"  user_songs rows: {self.rows} rebuilt, {self.rows_changed} {verb}changed, {self.rows_missing} missing",
            f"  sung_surveys entries: +{self.entries_added} / -{self.entries_removed}",
            f"  voice-bucket lookups: {self.bucket_lookups}",
            f"  song_crowd_stats: {self.crowd_songs} songs with votes; {self.crowd_written} {verb}written, "
            f"{self.crowd_unchanged} unchanged, {self.crowd_deleted} stale {verb}deleted",
        ]


def _norm_counts(raw: Any) -> dict[str, int]:
    raw = raw if isinstance(raw, dict) else {}
    return {k: int(raw.get(k) or 0) for k in KEY_ANSWERS}


def crowd_counts(stats: dict[str, Any] | None) -> tuple[Any, ...]:
    """The counts of one ``song_crowd_stats`` doc, comparable across writers: ``updated_at`` and
    all-zero buckets (which the live path leaves behind) don't matter."""
    stats = stats or {}
    raw = stats.get("by_bucket")
    by_bucket: dict[str, Any] = raw if isinstance(raw, dict) else {}
    buckets = tuple(
        (b, tuple(c.values())) for b in VOICE_BUCKETS if any((c := _norm_counts(by_bucket.get(b))).values())
    )
    return (
        stats.get("song_key"),
        tuple(_norm_counts(stats.get("total")).values()),
        buckets,
        int(stats.get("crowd_pleaser_yes") or 0),
        int(stats.get("crowd_pleaser_answered") or 0),
    )


def _entry_id(rec: dict[str, Any]) -> str:
    """The record's entry id (the ``sung_surveys`` key): its ``id`` field, else the part of the doc id
    after ``{user_id}:``."""
    eid = str(rec.get("id") or "")
    prefix = f"{rec.get('user_id')}:"
    return eid[len(prefix) :] if eid.startswith(prefix) else eid


class _Buckets:
    """Each singer's current voice bucket, looked up once (``find_user_doc``, the live path's rule)."""

    def __init__(self, fs: RebuildFirestore, summary: Summary) -> None:
        self._fs, self._summary = fs, summary
        self._cache: dict[str, str | None] = {}

    async def get(self, user_id: str) -> str | None:
        if user_id not in self._cache:
            self._summary.bucket_lookups += 1
            doc, _ = await find_user_doc(self._fs, user_id)
            self._cache[user_id] = voice_bucket((doc or {}).get(VOICE_PROFILE_FIELD))
        return self._cache[user_id]


async def rebuild(fs: RebuildFirestore, apply: bool = False, now: str | None = None) -> Summary:
    """Rebuild every row's survey state and all of ``song_crowd_stats`` (writes only with ``apply``)."""
    now = now or datetime.now(UTC).isoformat()
    summary = Summary(apply=apply)
    buckets = _Buckets(fs, summary)

    by_row: dict[str, list[dict[str, Any]]] = {}
    for doc in await fs.query_documents(LOG_COLLECTION):
        summary.log_records += 1
        counted = doc.get("counted") or {}
        if counted.get("collection") != USER_SONGS_COLLECTION or not counted.get("doc_id") or not _counts(doc):
            continue
        summary.counted_records += 1
        by_row.setdefault(str(counted["doc_id"]), []).append(doc)

    # 1. per-row survey map + derived fields
    rebuilt: list[dict[str, Any]] = []
    for row_id in sorted(by_row):
        records = by_row[row_id]
        row = await fs.get_document(USER_SONGS_COLLECTION, row_id)
        if row is None:
            summary.rows_missing += 1
            logger.warning("user_songs row %s is missing (%d log records count on it); skipped", row_id, len(records))
            continue
        summary.rows += 1
        old = _surveys(row)
        new_map: dict[str, Any] = {}
        for rec in sorted(records, key=_entry_id):
            eid = _entry_id(rec)
            prev = old.get(eid)
            bucket = await buckets.get(str(rec["user_id"])) if _key_is_new(rec, prev) else None
            entry = _survey_entry(rec, prev, bucket)
            if entry is not None:
                new_map[eid] = entry
        candidate = {**row, "sung_surveys": new_map}
        _derive_survey_fields(candidate)
        if not candidate.get("song_key"):
            candidate["song_key"] = next((r.get("song_key") for r in records if r.get("song_key")), None)
        rebuilt.append(candidate)
        summary.entries_added += len(set(new_map) - set(old))
        summary.entries_removed += len(set(old) - set(new_map))
        fields = {f: candidate[f] for f in DERIVED_FIELDS}
        if any(row.get(f) != v for f, v in fields.items()):
            summary.rows_changed += 1
            summary.changed_row_ids.append(row_id)
            if apply:
                await fs.update_document(USER_SONGS_COLLECTION, row_id, {**fields, "updated_at": now})

    # 2. song_crowd_stats from scratch: one vote per row
    stats: dict[str, dict[str, Any]] = {}
    for row in rebuilt:
        song_key = row.get("song_key")
        vote, pleaser = _crowd_vote(row), _crowd_pleaser(row)
        if not song_key or (vote is None and pleaser is None):
            continue
        key = str(song_key)
        s = apply_vote(stats.get(key) or {"song_key": key}, None, vote, now)
        stats[key] = apply_pleaser(s, None, pleaser, now)
    summary.crowd_songs = len(stats)

    existing = {str(d["id"]): d for d in await fs.query_documents(CROWD_COLLECTION)}
    wanted = {crowd_doc_id(k): s for k, s in stats.items()}
    for doc_id in sorted(wanted):
        s = wanted[doc_id]
        if doc_id in existing and crowd_counts(existing[doc_id]) == crowd_counts(s):
            summary.crowd_unchanged += 1
            continue
        summary.crowd_written += 1
        if apply:
            await fs.set_document(CROWD_COLLECTION, doc_id, s)
    for doc_id in sorted(set(existing) - set(wanted)):
        summary.crowd_deleted += 1
        if apply:
            await fs.delete_document(CROWD_COLLECTION, doc_id)
    return summary


def _firestore(project: str, database: str) -> RebuildFirestore:
    from backend.config import BackendSettings
    from backend.services.firestore_service import FirestoreService

    return FirestoreService(BackendSettings(google_cloud_project=project, firestore_database=database))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    parser.add_argument("--project", default="nomadkaraoke")
    parser.add_argument("--database", default="(default)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    summary = asyncio.run(rebuild(_firestore(args.project, args.database), apply=args.apply))
    for line in summary.lines():
        logger.info(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
