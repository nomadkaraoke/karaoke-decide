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
cases)`` compares the phone's offline identification with the cloud's (spec §8); it
judges "same song" with the matcher's own ``identify.same_song``.
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

from karaoke_decide.song_search.identify import SongIdentifier, same_song, same_title, shared_credit
from karaoke_decide.song_search.text_normalize import identity_normalize as normalize  # song identity


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


def _same(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    """Same song under ``identify.same_song`` (base title + a shared credit): "Redneck
    Woman (live)" = "Redneck Woman", "Michael Andrews & Gary Jules" = "… feat. Gary Jules"."""
    return a is not None and b is not None and same_song(a["artist"], a["title"], b["artist"], b["title"])


def _labelled(case: dict[str, Any]) -> bool:
    return bool(case.get("artist") or case.get("title"))


def _label_wrong(case: dict[str, Any], r: dict[str, Any]) -> bool:
    """Confident on a song that is not the case's label (``same_song`` semantics; a
    label with only a title or only an artist checks just that part)."""
    if r["status"] != "confident":
        return False
    b = r["best"]
    if case.get("title") and not same_title(case["title"], b["title"]):
        return True
    return bool(case.get("artist")) and not shared_credit(case["artist"], b["artist"])


# Disagreement types, in report order: the gated one first.
CONFIDENT_DISAGREE = "confident_disagree"
OFFLINE_CONFIDENT_CLOUD_UNSURE_OTHER = "offline_confident_cloud_unsure_other"
OFFLINE_MORE_CONFIDENT = "offline_more_confident"
OFFLINE_DOWNGRADED = "offline_downgraded"
DISAGREEMENT_ORDER = (
    CONFIDENT_DISAGREE,
    OFFLINE_CONFIDENT_CLOUD_UNSURE_OTHER,
    OFFLINE_MORE_CONFIDENT,
    OFFLINE_DOWNGRADED,
)


def classify_pair(cloud: dict[str, Any], offline: dict[str, Any]) -> str | None:
    """One query's cloud vs offline relation (``{status, best}`` each; ``best`` needs
    ``artist`` and ``title``), or None when they agree / neither is confident.

    ``confident_disagree``: both confident, different songs (``_same``) — gated.
    ``offline_confident_cloud_unsure_other``: offline confident, the cloud is not and its
    best guess (if any) is another song. ``offline_more_confident``: offline confident on
    the cloud's own (unconfident) best guess. ``offline_downgraded``: cloud confident,
    offline not (the price of the smaller index and the stricter margin)."""
    oc, cc = offline["status"] == "confident", cloud["status"] == "confident"
    same = _same(cloud.get("best"), offline.get("best"))
    if oc and cc:
        return None if same else CONFIDENT_DISAGREE
    if oc:
        return OFFLINE_MORE_CONFIDENT if same else OFFLINE_CONFIDENT_CLOUD_UNSURE_OTHER
    if cc:
        return OFFLINE_DOWNGRADED
    return None


def dual_eval(cloud: Identifier, offline: Identifier, cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Offline vs cloud identification over the same queries (spec §8 dual eval).

    → ``{total, confident_disagree, offline_confident_cloud_unsure_other,
    offline_more_confident, offline_downgraded, downgraded_cloud_best_karaoke,
    downgraded_cloud_best_non_karaoke, labelled, cloud_wrong_auto, offline_wrong_auto,
    disagreements, wrong_auto_cases}``.

    Per-query types are ``classify_pair``'s. Only ``confident_disagree`` (both sides sure,
    different songs) is gated by rate; the two "offline sure, cloud unsure" counts are
    informational. ``offline_downgraded`` is split by the cloud answer: a karaoke song
    (offline lost confidence on a song it holds) vs a non-karaoke one (mostly not on the
    phone at all). Over labelled cases (an artist and/or title), ``cloud_wrong_auto`` /
    ``offline_wrong_auto`` count confident answers that are not the label; the build gates
    offline against cloud. ``disagreements`` lists ``{q, type, cloud, offline}`` (each side
    ``{status, artist, title}``) ordered by ``DISAGREEMENT_ORDER``; ``wrong_auto_cases``
    lists ``{q, want, cloud_wrong, offline_wrong, cloud, offline}`` where either side is
    wrong. ``offline`` is built with ``margin_bonus=offline.OFFLINE_MARGIN_BONUS``."""
    counts: Counter[str] = Counter()
    downgraded_karaoke = labelled = cloud_wrong = offline_wrong = 0
    disagreements: list[dict[str, Any]] = []
    wrong_cases: list[dict[str, Any]] = []
    for c in cases:
        rc, ro = cloud.identify(c["q"]), offline.identify(c["q"])
        if _labelled(c):
            labelled += 1
            cw, ow = _label_wrong(c, rc), _label_wrong(c, ro)
            cloud_wrong += cw
            offline_wrong += ow
            if cw or ow:
                wrong_cases.append(
                    {
                        "q": c["q"],
                        "want": {"artist": c.get("artist"), "title": c.get("title")},
                        "cloud_wrong": cw,
                        "offline_wrong": ow,
                        "cloud": _brief(rc),
                        "offline": _brief(ro),
                    }
                )
        kind = classify_pair(rc, ro)
        if kind is None:
            continue
        counts[kind] += 1
        if kind == OFFLINE_DOWNGRADED and rc["best"].get("karaoke"):
            downgraded_karaoke += 1
        disagreements.append({"q": c["q"], "type": kind, "cloud": _brief(rc), "offline": _brief(ro)})
    disagreements.sort(key=lambda d: DISAGREEMENT_ORDER.index(d["type"]))
    return {
        "total": len(cases),
        CONFIDENT_DISAGREE: counts[CONFIDENT_DISAGREE],
        OFFLINE_CONFIDENT_CLOUD_UNSURE_OTHER: counts[OFFLINE_CONFIDENT_CLOUD_UNSURE_OTHER],
        OFFLINE_MORE_CONFIDENT: counts[OFFLINE_MORE_CONFIDENT],
        OFFLINE_DOWNGRADED: counts[OFFLINE_DOWNGRADED],
        "downgraded_cloud_best_karaoke": downgraded_karaoke,
        "downgraded_cloud_best_non_karaoke": counts[OFFLINE_DOWNGRADED] - downgraded_karaoke,
        "labelled": labelled,
        "cloud_wrong_auto": cloud_wrong,
        "offline_wrong_auto": offline_wrong,
        "disagreements": disagreements,
        "wrong_auto_cases": wrong_cases,
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
