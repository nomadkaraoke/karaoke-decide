#!/usr/bin/env python3
"""Export one voice session from Firestore as a replay fixture (numbers only). Read-only.

Reads ``decide_users/{sha256(lowercase email)}`` (for the user id), ``voice_sessions/{session}`` and the
session's ``voice_attempts``, and writes what ``karaoke_decide.voice_session.replay.replay_fixture`` reads::

    {schema, notes, setup, prior_profile, attempts: [{n, song_key, demand, quiz, pitch, context, stopped_early}]}

``song_key`` becomes ``sha1(song_key)[:10]``; ``demand`` is the attempt's stored song snapshot demand
(``{low, high, p10, p50, p90}``, null if the snapshot has none). No titles, artists, video ids, device names,
user or session ids are written, and only the whitelisted quiz / context / pitch / profile fields.

Needs read access to prod Firestore (Andrew's credentials, ``/gcp-admin``):

  python scripts/voice/export_session_fixture.py --email <e> --session <id> \\
      --out tests/unit/voice_session/fixtures/<name>.json
  python scripts/voice/export_session_fixture.py --email <e> --session <id> --dry-run   # print, write nothing
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

USERS = "decide_users"
SESSIONS = "voice_sessions"
ATTEMPTS = "voice_attempts"
SCHEMA = 1

_QUIZ_KEYS = ("feel", "position", "difficulty", "knew", "would_sing", "would_work_if", "voice_now",
              "version_quality", "version_issues", "tags")  # fmt: skip
_CONTEXT_KEYS = ("posture", "warmth", "headphones")
_SETUP_KEYS = ("posture", "warmth", "headphones", "record")
_PITCH_KEYS = ("voiced_s", "held_count", "held_low", "held_high", "p10", "p50", "p90")
_DEMAND_KEYS = ("low", "high", "p10", "p50", "p90")
_PROFILE_KEYS = ("method", "voice_type", "voice_type_between", "register", "low_midi", "high_midi",
                 "comfort_low", "comfort_high", "confidence", "evidence_count")  # fmt: skip


class ExportError(Exception):
    pass


def email_hash(email: str) -> str:
    """``decide_users`` doc id (``AuthService._hash_email`` / ``user_deletion.email_hash``)."""
    return hashlib.sha256(email.lower().encode()).hexdigest()


def short_key(song_key: str) -> str:
    return hashlib.sha1(song_key.encode()).hexdigest()[:10]


def _pick(d: Mapping[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any] | None:
    if not d:
        return None
    return {k: d.get(k) for k in keys}


def _numbers(d: Mapping[str, Any] | None, keys: tuple[str, ...]) -> dict[str, float] | None:
    if not d or any(d.get(k) is None for k in keys):
        return None
    return {k: float(d[k]) for k in keys}


def export_attempt(a: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "n": int(a["n"]),
        "song_key": short_key(str(a["song_key"])),
        "demand": _numbers((a.get("song") or {}).get("demand"), _DEMAND_KEYS),
        "quiz": _pick(a.get("quiz"), _QUIZ_KEYS),
        "pitch": _numbers(a.get("pitch"), _PITCH_KEYS),
        "context": _pick(a.get("context"), _CONTEXT_KEYS),
        "stopped_early": bool(a.get("stopped_early")),
    }


def build_fixture(db: Any, email: str, session_id: str) -> dict[str, Any]:
    """The fixture for one session of the user with this email (only ``get`` / ``where().stream()`` reads)."""
    user = db.collection(USERS).document(email_hash(email)).get().to_dict() or {}
    user_id = user.get("user_id")
    if not user_id:
        raise ExportError("no decide_users doc for that email")
    session = db.collection(SESSIONS).document(session_id).get().to_dict()
    if not session or session.get("user_id") != user_id:
        raise ExportError("no such session for that user")
    docs = [s.to_dict() or {} for s in db.collection(ATTEMPTS).where("session_id", "==", session_id).stream()]
    attempts = sorted((export_attempt(a) for a in docs if a.get("user_id") == user_id), key=lambda a: a["n"])
    skips = session.get("skips") or []
    return {
        "schema": SCHEMA,
        "notes": [
            f"Exported by scripts/voice/export_session_fixture.py; state {session.get('state')}, "
            f"{len(attempts)} attempts, {len(skips)} skips (skips never move the posterior).",
            "song_key is sha1(song_key)[:10]; numbers only.",
        ],
        "setup": _pick(session.get("setup"), _SETUP_KEYS) or {},
        "prior_profile": _pick(session.get("prior_profile"), _PROFILE_KEYS),
        "attempts": attempts,
    }


def main(argv: list[str] | None = None, *, db: Any = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--email", required=True)
    ap.add_argument("--session", required=True)
    out = ap.add_mutually_exclusive_group(required=True)
    out.add_argument("--out", type=Path, help="fixture path to write")
    out.add_argument("--dry-run", action="store_true", help="print the fixture, write nothing")
    ap.add_argument("--project", default="nomadkaraoke")
    args = ap.parse_args(argv)

    if db is None:
        from google.cloud import firestore

        db = firestore.Client(project=args.project)
    try:
        fixture = build_fixture(db, args.email, args.session)
    except ExportError as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(fixture, indent=2) + "\n"
    if args.dry_run:
        print(text, end="")
    else:
        args.out.write_text(text)
        print(f"wrote {args.out} ({len(fixture['attempts'])} attempts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
