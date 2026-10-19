# Vendored from kjbox kj-controller/scripts/build_song_id_db.py @ 8973e4b; keep in step (parity tests in tests/unit/song_search/).
"""Build the song-identification index (``song_id.db``).

Input: the TSV(.gz) export of "every song people might mean" — columns
``artist, title, popularity, karaoke`` (popularity blank for karaoke-only rows).
See kjbox docs/SONG-IDENTIFICATION.md §3 for where it comes from.

Output SQLite:
  songs(rowid, artist, title, na, nt, pop, karaoke)      display + normalized text
  songs_fts  — FTS5 word index over (na, nt)             candidate retrieval
  artists(na, artist, pop, songs) + artists_fts          artist-first path (word index over names)
  vocab(word, freq, first, len)                          typo lookup: same first letter, similar length
  meta(key, value)                                       schema/normalizer versions, counts, built_at, run

Duplicates are merged on the space-less normalized artist+title ("u s a" == "usa"),
keeping the most popular display spelling (on a tie, one without a file tag such as
"(MPX)") and OR-ing the karaoke flag. Then spelling
variants of the same artist's title are folded together ("Breaking Dishes" ==
"Breakin' Dishes", "Get Your Freak On" == "Get Ur Freak On"), showing the spelling
that has a karaoke version (the name singers will find), else the more popular one.

Decide-side change from kjbox: ``build(tsv_paths, out_path, run)`` takes Paths and a
run id (stored in ``meta``) and returns ``{songs, words, built_at, run}``.

Usage: python -m karaoke_decide.song_search.build_song_id [--run RUN] songs.tsv.gz [more.tsv.gz ...] song_id.db
"""

from __future__ import annotations

import argparse
import csv
import gzip
import os
import re
import sqlite3
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any

from karaoke_decide.song_search.identify import song_norm
from karaoke_decide.song_search.text_normalize import FILE_TAG_RE, NORMALIZER_VERSION

SCHEMA_VERSION = 3


_JUNK_CREDIT_RE = re.compile(r"\b(?:cover|covers|covered|tribute|karaoke|in the style of|made famous)\b", re.IGNORECASE)


# Title spelling folds: dropped g ("walkin" = "walking") and text-speak ("ur", "u").
# Only 6+ letter "-ing" words fold: shorter ones collide with real words ("thing"/"thin").
_WORD_FOLDS = {"ur": "your", "u": "you"}


def _fold_title(nt: str) -> str:
    """Space-less title with spelling variants folded (merge key only, never displayed)."""
    return "".join(_WORD_FOLDS.get(w, w[:-1] if len(w) >= 6 and w.endswith("ing") else w) for w in nt.split())


def _exact_key(artist: str, title: str) -> tuple[str, str]:
    """(space-less artist, title) norms before spelling folds; file-quality tags
    ("(MPX)", "[HM]") dropped (decide-side: kjbox keeps them in the key)."""
    na = song_norm(FILE_TAG_RE.sub(" ", artist)).replace(" ", "")
    nt = song_norm(FILE_TAG_RE.sub(" ", title))
    return na, nt


def merge_key(artist: str, title: str) -> str:
    """The key ``build`` merges songs on: space-less artist norm + spelling-folded
    title. Every spelling of one ``song_id.db`` song shares it (the availability
    index resolves KN rows to song-id songs through it)."""
    na, nt = _exact_key(artist, title)
    return f"{na}|{_fold_title(nt)}"


def _untagged(artist: str, title: str) -> int:
    """1 when the spelling carries no file-quality tag ("(MPX)", "[HM]"): between
    otherwise tied spellings the untagged one is shown (decide-side)."""
    return 0 if FILE_TAG_RE.search(artist) or FILE_TAG_RE.search(title) else 1


def _open(path: Path) -> IO[str]:
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, encoding="utf-8")


def _rows(srcs: list[Path]) -> Iterator[list[str]]:
    for src in srcs:
        with _open(src) as f:
            yield from csv.reader(f, delimiter="\t")


# A merged song: [artist, title, popularity | None, karaoke 0/1]
_Song = list[Any]


def build(tsv_paths: list[Path], out_path: Path, run: str) -> dict[str, Any]:
    """Build ``out_path`` from one or more TSV(.gz) shards; atomic replace.

    → ``{songs, words, built_at, run}`` (also stored in the ``meta`` table)."""
    srcs = [Path(p) for p in tsv_paths]
    dst = str(out_path)
    merged: dict[str, _Song] = {}
    folds: dict[str, str] = {}  # exact key → spelling-folded key (merge_key), only where they differ
    for row in _rows(srcs):
        if len(row) < 4:
            continue
        artist, title, pop, karaoke = row[0].strip(), row[1].strip(), row[2], row[3]
        na, nt = song_norm(artist), song_norm(title)
        if not na or not nt:
            continue
        p = int(pop) if pop.strip() else None
        k = 1 if karaoke.strip() in ("1", "true", "True") else 0
        if not k and _JUNK_CREDIT_RE.search(artist):
            continue  # MusicBrainz cover/tribute uploads ("George Benson (Hscc Cover Ft …)")
        if na.replace(" ", "") == nt.replace(" ", "") and not k and (p or 0) < 40:
            continue  # MusicBrainz "self-titled" junk ("Future — Future"); real ones have karaoke/popularity
        ena, ent = _exact_key(artist, title)
        key = f"{ena}|{ent.replace(' ', '')}"
        cur = merged.get(key)
        if cur is None:
            merged[key] = [artist, title, p, k]
            folded = merge_key(artist, title)
            if folded != key:
                folds[key] = folded
        else:
            if p is not None and (cur[2] is None or p > cur[2]):
                cur[0], cur[1], cur[2] = artist, title, p
            elif p == cur[2] and _untagged(artist, title) > _untagged(cur[0], cur[1]):
                cur[0], cur[1] = artist, title  # tie (incl. both blank): untagged spelling shows
            cur[3] = cur[3] or k
    # Fold spelling variants into one song. Folding is idempotent, so a folded key is
    # either an unfolded song's own key or a new group started by the first variant.
    # Between spellings: the one with a karaoke version, then the more popular (both
    # "Get Your Freak On" and "Get Ur Freak On" are on KaraokeNerds) — ranked by the
    # shown spelling's own popularity, not the group's running max. Decide-side: on a
    # tie, the spelling without a file tag ("(MPX)", "[HM]").
    shown: dict[str, tuple[int, int, int]] = {}  # folded key → rank of the spelling currently displayed
    for key, fkey in folds.items():
        cur = merged.pop(key)
        rank = (cur[3], cur[2] if cur[2] is not None else -1, _untagged(cur[0], cur[1]))
        target = merged.get(fkey)
        if target is None:
            merged[fkey] = cur
            shown[fkey] = rank
        else:
            if fkey not in shown:
                shown[fkey] = (target[3], target[2] if target[2] is not None else -1, _untagged(target[0], target[1]))
            if rank > shown[fkey]:
                target[0], target[1] = cur[0], cur[1]
                shown[fkey] = rank
            if cur[2] is not None and (target[2] is None or cur[2] > target[2]):
                target[2] = cur[2]
            target[3] = target[3] or cur[3]
    del folds, shown

    tmp = dst + ".new"
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.executescript("""
        PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE songs(rowid INTEGER PRIMARY KEY, artist TEXT, title TEXT, na TEXT, nt TEXT,
                           pop INTEGER, karaoke INTEGER);
        CREATE VIRTUAL TABLE songs_fts USING fts5(na, nt, content='songs', content_rowid='rowid',
                                                  tokenize='unicode61');
        CREATE TABLE artists(rowid INTEGER PRIMARY KEY, na TEXT UNIQUE, artist TEXT, pop INTEGER, songs INTEGER);
        CREATE VIRTUAL TABLE artists_fts USING fts5(na, content='artists', content_rowid='rowid', tokenize='unicode61');
        CREATE TABLE vocab(word TEXT PRIMARY KEY, freq INTEGER, first TEXT, len INTEGER) WITHOUT ROWID;
    """)
    vocab: Counter[str] = Counter()
    rows = []
    artists: dict[str, list[Any]] = {}
    for artist, title, p, k in merged.values():
        na, nt = song_norm(artist), song_norm(title)
        rows.append((artist, title, na, nt, p, k))
        vocab.update(set(na.split()) | set(nt.split()))
        a = artists.setdefault(na, [artist, -1, 0])
        a[2] += 1
        if (p if p is not None else 30) > a[1]:
            a[0], a[1] = artist, (p if p is not None else 30)
    db.executemany("INSERT INTO songs(artist, title, na, nt, pop, karaoke) VALUES (?,?,?,?,?,?)", rows)
    db.execute("CREATE INDEX songs_na ON songs(na)")
    db.execute("INSERT INTO songs_fts(songs_fts) VALUES ('rebuild')")
    db.executemany(
        "INSERT INTO artists(na, artist, pop, songs) VALUES (?,?,?,?)",
        [(na, a, p, n) for na, (a, p, n) in artists.items()],
    )
    db.execute("INSERT INTO artists_fts(artists_fts) VALUES ('rebuild')")
    db.executemany("INSERT INTO vocab VALUES (?,?,?,?)", [(w, f, w[0], len(w)) for w, f in vocab.items()])
    db.execute("CREATE INDEX vocab_first_len ON vocab(first, len)")
    db.execute("INSERT INTO songs_fts(songs_fts) VALUES ('optimize')")
    built_at = int(time.time())
    db.executemany(
        "INSERT INTO meta VALUES (?,?)",
        [
            ("schema_version", str(SCHEMA_VERSION)),
            ("songs", str(len(rows))),
            ("words", str(len(vocab))),
            ("normalizer_version", str(NORMALIZER_VERSION)),
            ("built_at", str(built_at)),
            ("run", run),
        ],
    )
    db.commit()
    db.execute("VACUUM")
    db.close()
    os.replace(tmp, dst)
    return {"songs": len(rows), "words": len(vocab), "built_at": built_at, "run": run}


def stored_meta(db_path: str | Path) -> dict[str, str]:
    """meta table of an existing index ({} if missing/unreadable)."""
    if not os.path.exists(db_path):
        return {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            return dict(conn.execute("SELECT key, value FROM meta").fetchall())
        finally:
            conn.close()
    except sqlite3.Error:
        return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build song_id.db from TSV(.gz) shards.")
    parser.add_argument("--run", default="local", help="run id stored in meta (default: local)")
    parser.add_argument("tsv", nargs="+", type=Path, help="input shards")
    parser.add_argument("out", type=Path, help="output song_id.db")
    args = parser.parse_args(argv)
    out = build(args.tsv, args.out, args.run)
    print(f"{out['songs']} songs, {out['words']} words → {args.out} ({os.path.getsize(args.out) / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
