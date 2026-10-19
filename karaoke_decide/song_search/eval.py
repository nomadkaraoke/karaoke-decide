# Vendored from kjbox kj-controller/scripts/song_id_eval.py @ 8973e4b; keep in step (parity tests in tests/unit/song_search/).
"""Score the song identifier against a labelled test set.

Usage:
  python -m karaoke_decide.song_search.eval --db song_id.db --cases tests/fixtures/song_search/song_id_eval.jsonl
  python -m karaoke_decide.song_search.eval --db song_id.db --synthetic 150 [--verbose]

--synthetic N: instead of the labelled set, sample N popular karaoke songs from the
index and generate "drunk" variants (typos, title only, artist fragment, dropped
apostrophes, doubled/dropped letters, keyboard slips). A held-out check: the
matcher was never tuned on these. (The frozen held-out set is
tests/fixtures/song_search/song_id_synthetic.jsonl — pass it as --cases.)

Per case:
  confident-correct  — auto-applied the right song                 (goal)
  confident-WRONG    — auto-applied a wrong song                   (must be ~0)
  cand-correct       — right song in the "Which one?" candidates
  cand-miss          — candidates shown, right song not among them
  cand-ok            — candidates for a negative case (fine)
  none               — no local answer (→ Gemini fallback)
For negatives (no artist/title), "none" or "cand" is fine; confident is WRONG.

Decide-side change from kjbox: the scoring loop is ``run_eval(identifier, cases)``,
returning ``{total, auto, wrong_auto, candidates, none, wrong}`` for the index-build
job's eval gate; the CLI prints kjbox's report on top of it. ``dual_eval(cloud, offline,
cases)`` compares the phone's offline identification with the cloud's (spec §8).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Protocol

from karaoke_decide.song_search.identify import SongIdentifier
from karaoke_decide.song_search.text_normalize import identity_normalize as normalize  # song identity
from karaoke_decide.song_search.text_normalize import song_key


def _key(s: str | None) -> str:
    return normalize(s).replace(" ", "")


_CREDIT_SPLIT_RE = re.compile(r"\s*(?:,|&|\+|/|\bfeaturing\b|\bfeat\.?|\bft\.?|\bx\b|\band\b|\bwith\b)\s*", re.I)
_VERSION_RE = re.compile(r"\s*[\(\[].*$|\s+-\s+.*$")


def _credits(artist: str | None) -> set[str]:
    return {_key(p) for p in _CREDIT_SPLIT_RE.split(artist or "") if _key(p)}


def _is(case: dict[str, Any], m: dict[str, Any] | None) -> bool:
    """Same song as a person would judge it: same title (ignoring "(feat. …)" /
    "- Remastered" suffixes) and at least one credited artist in common
    ("Cashmere Cat feat. Ariana Grande" ~ "Ariana Grande")."""
    if m is None:
        return False
    if case["title"] and _key(_VERSION_RE.sub("", case["title"])) != _key(_VERSION_RE.sub("", m["title"])):
        return False
    if case["artist"] and not (_credits(case["artist"]) & _credits(m["artist"])):
        return False
    return True


def outcome(case: dict[str, Any], result: dict[str, Any]) -> str:
    """One case's outcome (see the module docstring)."""
    negative = not case["title"] and not case["artist"]
    if result["status"] == "confident":
        return "confident-WRONG" if negative or not _is(case, result["best"]) else "confident-correct"
    if result["status"] == "candidates":
        if negative:
            return "cand-ok"
        return "cand-correct" if any(_is(case, m) for m in result["candidates"]) else "cand-miss"
    return "none"


def run_eval(identifier: SongIdentifier, cases: list[dict[str, Any]]) -> dict[str, Any]:
    """→ ``{total, auto, wrong_auto, candidates, none, wrong}``.

    ``auto`` counts confident-correct answers, ``wrong_auto`` confident-WRONG ones
    (listed in ``wrong`` as ``{q, kind, want, got}``); ``candidates`` and ``none``
    count the other statuses."""
    outcomes: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    wrong = []
    for c in cases:
        r = identifier.identify(c["q"])
        o = outcome(c, r)
        outcomes[o] += 1
        statuses[r["status"]] += 1
        if o == "confident-WRONG":
            b = r["best"]
            wrong.append(
                {
                    "q": c["q"],
                    "kind": c.get("kind"),
                    "want": {"artist": c["artist"], "title": c["title"]},
                    "got": {"artist": b["artist"], "title": b["title"], "score": b["score"]},
                }
            )
    return {
        "total": len(cases),
        "auto": outcomes["confident-correct"],
        "wrong_auto": outcomes["confident-WRONG"],
        "candidates": statuses["candidates"],
        "none": statuses["none"],
        "wrong": wrong,
    }


class Identifier(Protocol):
    def identify(self, query: str, limit: int = 5) -> dict[str, Any]: ...


def _brief(r: dict[str, Any]) -> dict[str, Any]:
    b = r.get("best")
    return {"status": r["status"], "artist": b["artist"] if b else None, "title": b["title"] if b else None}


def dual_eval(cloud: Identifier, offline: Identifier, cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Offline vs cloud identification over the same queries (spec §8 dual eval).

    → ``{total, confident_disagree, offline_downgraded, downgraded_cloud_best_karaoke,
    downgraded_cloud_best_non_karaoke, disagreements}``.

    ``confident_disagree``: offline is confident but the cloud is not confident about
    the same song (``song_key``) — it names another song, or the cloud asks "Which
    one?" / has nothing. Must stay ~0: offline naming the wrong song is an anti-goal.
    ``offline_downgraded``: the cloud is confident, offline is not (it asks instead);
    allowed, it is the price of the smaller index and the stricter margin. It is split
    by the cloud answer: a karaoke song (offline lost confidence on a song it holds)
    vs a non-karaoke song (expected: most are not on the phone at all).
    ``disagreements`` lists both kinds: ``{q, type, cloud, offline}`` with each side's
    ``{status, artist, title}``. ``offline`` is built with
    ``margin_bonus=offline.OFFLINE_MARGIN_BONUS``."""
    confident_disagree = downgraded = downgraded_karaoke = 0
    disagreements: list[dict[str, Any]] = []
    for c in cases:
        rc, ro = cloud.identify(c["q"]), offline.identify(c["q"])
        same = (
            rc["best"] is not None
            and ro["best"] is not None
            and song_key(rc["best"]["artist"], rc["best"]["title"])
            == song_key(ro["best"]["artist"], ro["best"]["title"])
        )
        if ro["status"] == "confident" and not (rc["status"] == "confident" and same):
            kind = "confident_disagree"
            confident_disagree += 1
        elif rc["status"] == "confident" and ro["status"] != "confident":
            kind = "offline_downgraded"
            downgraded += 1
            downgraded_karaoke += 1 if rc["best"].get("karaoke") else 0
        else:
            continue
        disagreements.append({"q": c["q"], "type": kind, "cloud": _brief(rc), "offline": _brief(ro)})
    return {
        "total": len(cases),
        "confident_disagree": confident_disagree,
        "offline_downgraded": downgraded,
        "downgraded_cloud_best_karaoke": downgraded_karaoke,
        "downgraded_cloud_best_non_karaoke": downgraded - downgraded_karaoke,
        "disagreements": disagreements,
    }


_KEYS = "qwertyuiop asdfghjkl zxcvbnm"
_NEAR = {
    c: (_KEYS[i - 1] if i else "") + (_KEYS[i + 1] if i + 1 < len(_KEYS) else "")
    for i, c in enumerate(_KEYS)
    if c != " "
}


def _typo(word: str, rng: random.Random) -> str:
    if len(word) < 4:
        return word
    i = rng.randrange(1, len(word) - 1)
    kind = rng.choice(["drop", "double", "swap", "near"])
    if kind == "drop":
        return word[:i] + word[i + 1 :]
    if kind == "double":
        return word[:i] + word[i] + word[i:]
    if kind == "swap":
        return word[:i] + word[i + 1] + word[i] + word[i + 2 :]
    near = _NEAR.get(word[i].lower(), "").replace(" ", "")
    return word[:i] + (rng.choice(near) if near else word[i]) + word[i + 1 :]


def _typo_longest(text: str, rng: random.Random) -> str:
    words = text.split()
    if not words:
        return text
    i = max(range(len(words)), key=lambda k: len(words[k]))
    words[i] = _typo(words[i], rng)
    return " ".join(words)


def synthetic_cases(db_path: str | Path, n: int, seed: int = 7) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        # Seeded sample (SQLite's random() ignores the seed): same songs every run for a
        # given index, so before/after comparisons are like for like.
        pool = db.execute(
            "SELECT artist, title FROM songs WHERE karaoke = 1 AND pop >= 55 "
            "AND length(title) BETWEEN 4 AND 40 ORDER BY lower(artist), lower(title)"
        ).fetchall()
    finally:
        db.close()
    songs = rng.sample(pool, min(n, len(pool)))
    cases = []
    for artist, title in songs:
        t, a = title.lower(), artist.lower()
        a_frag = rng.choice(a.split()) if a.split() else a
        for q, kind in [
            (t, "syn-title-only"),
            (f"{t} {a}", "syn-exact"),
            (f"{a} {_typo_longest(t, rng)}", "syn-typo-title"),
            (f"{_typo_longest(a, rng)} {t}", "syn-typo-artist"),
            (f"{t} {a_frag}", "syn-artist-fragment"),
            (f"{_typo_longest(t, rng)} {a_frag}".replace("'", ""), "syn-fragment+typo"),
        ]:
            # Title-only keeps the sampled artist as the expected answer: a confident
            # answer naming ANOTHER artist's same-title song is wrong for the singer
            # (it would pre-fill the wrong artist), so it must not score as correct.
            cases.append({"q": q, "artist": artist, "title": title, "kind": kind, "source": "synthetic"})
    return cases


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score song identification against labelled cases.")
    parser.add_argument("--db", required=True, type=Path, help="song_id.db to evaluate")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--cases", type=Path, help="labelled JSONL cases")
    src.add_argument("--synthetic", type=int, metavar="N", help="generate cases from N sampled index songs")
    parser.add_argument("--margin-bonus", type=float, default=0.0, help="offline margin bonus (default 0)")
    parser.add_argument("--verbose", action="store_true", help="print every case, not just misses")
    args = parser.parse_args(argv)

    ident = SongIdentifier(args.db, margin_bonus=args.margin_bonus)
    cases = synthetic_cases(args.db, args.synthetic) if args.synthetic else load_cases(args.cases)
    ident.identify("warm up")
    by_kind: defaultdict[str, Counter[str]] = defaultdict(Counter)
    total: Counter[str] = Counter()
    times = []
    for c in cases:
        t0 = time.perf_counter()
        r = ident.identify(c["q"])
        times.append((time.perf_counter() - t0) * 1000)
        o = outcome(c, r)
        by_kind[c["kind"]][o] += 1
        total[o] += 1
        if args.verbose or o in ("confident-WRONG", "cand-miss", "none"):
            b = r["best"]
            got = f"{b['artist']} — {b['title']} ({b['score']} {b.get('detail')})" if b else "-"
            print(f"{o:18} [{c['kind']}] {c['q']!r} → {got}   want: {c['artist']} — {c['title']}")
    print()
    for kind, cnt in sorted(by_kind.items()):
        print(f"{kind:22} " + "  ".join(f"{k}={v}" for k, v in sorted(cnt.items())))
    n = len(cases)
    if not n:
        print("no cases")
        return 1
    print(f"\nTOTAL {n}: " + "  ".join(f"{k}={v} ({v / n:.0%})" for k, v in sorted(total.items())))
    print(
        f"latency ms: p50={statistics.median(times):.0f} p95={sorted(times)[max(0, int(len(times) * 0.95) - 1)]:.0f}"
        f" max={max(times):.0f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
