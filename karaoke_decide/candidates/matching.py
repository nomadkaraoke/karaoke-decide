"""Artist/title normalization and fuzzy matching for candidate dedup.

Ported (and lightly refactored) from the original workspace
``scripts/karaoke-candidates/build_candidates.py`` matcher, which was tuned
against the real KaraokeNerds catalog. The goal is to collapse the many ways
the same song is written (case, punctuation, ``&`` vs ``and``, remaster/live/
feat. suffixes, optional leading "The") so we can reliably tell whether a
Last.fm track already exists in a karaoke catalog or in our own jobs.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any

_PAREN_JUNK = re.compile(
    r"\s*[\(\[][^)\]]*(remaster|remix|live|edit|version|mix|mono|stereo|deluxe|"
    r"feat\.|featuring|ft\.|acoustic|demo|single|album|radio|explicit|clean|"
    r"bonus|session|extended|instrumental|re-?record)[^)\]]*[\)\]]",
    re.I,
)
_DASH_JUNK = re.compile(
    r"\s+-\s+.*(remaster|remix|live|edit|version|mix|mono|stereo|deluxe|"
    r"radio|single|acoustic|demo|bonus|session|extended|re-?record).*$",
    re.I,
)
_FEAT = re.compile(r"\s+(feat\.?|featuring|ft\.?|with)\s+.*$", re.I)


def fold(s: str) -> str:
    """Strip diacritics (café -> cafe)."""
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def norm(s: str) -> str:
    """Aggressive normalization for building match keys."""
    s = fold(s).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[’'`]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def strip_decorations(title: str) -> str:
    """Return a human-readable title with remaster/feat./etc. cruft removed."""
    t = _PAREN_JUNK.sub("", title)
    t = _DASH_JUNK.sub("", t)
    return _FEAT.sub("", t).strip() or title


def title_variants(title: str) -> set[str]:
    """Normalized title variants (raw, suffix-stripped, feat-stripped)."""
    variants = {title}
    t = _PAREN_JUNK.sub("", title)
    variants.add(t)
    t2 = _DASH_JUNK.sub("", t)
    variants.add(t2)
    variants.add(_FEAT.sub("", t2))
    # also strip ANY trailing parenthetical as a last-resort variant
    variants.add(re.sub(r"\s*[\(\[][^)\]]*[\)\]]\s*$", "", title))
    return {norm(v) for v in variants if norm(v)}


def artist_variants(artist: str) -> set[str]:
    """Normalized artist variants (feat-stripped, first-of-list, +/- 'The')."""
    variants = {artist, _FEAT.sub("", artist)}
    # first artist of a comma / x / and / slash separated list
    variants.add(re.split(r",| x | & | and |;|/", artist, maxsplit=1, flags=re.I)[0])
    out = {norm(v) for v in variants if norm(v)}
    for v in list(out):
        if v.startswith("the "):
            out.add(v[4:])
        else:
            out.add("the " + v)
    if not out:  # names like "!!!" normalize to nothing
        out = {artist.lower().strip() or "unknown"}
    return out


def canonical_key(artist: str, title: str) -> tuple[str, str]:
    """Deterministic (artist, title) key: shortest normalized variant of each.

    Used for merging Last.fm duplicates and as the reject-list key.
    """
    a = sorted(artist_variants(artist))[0]
    tvars = sorted(title_variants(title), key=len)
    t = tvars[0] if tvars else (norm(title) or "unknown")
    return (a, t)


def build_match_index(rows: list[tuple[str, str]]) -> set[tuple[str, str]]:
    """Build a set of (artist, title) match keys covering all variants."""
    index: set[tuple[str, str]] = set()
    for artist, title in rows:
        for a in artist_variants(artist):
            for t in title_variants(title):
                index.add((a, t))
    return index


def index_contains(index: set[tuple[str, str]], artist: str, title: str) -> bool:
    """True if any artist/title variant pair is present in the match index."""
    return any((a, t) in index for a in artist_variants(artist) for t in title_variants(title))


def build_payload_index(
    rows: Iterable[tuple[str, str, dict[str, Any]]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Map every artist/title variant pair to the source rows behind it.

    Like :func:`build_match_index`, but retrieval-oriented: each variant key
    keeps the payload(s) it came from so a later lookup can return the matched
    catalog rows (e.g. brand + watch URL), not just a yes/no. One song can
    contribute several rows (multiple community brands), so values are lists.
    """
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for artist, title, payload in rows:
        for a in artist_variants(artist):
            for t in title_variants(title):
                index.setdefault((a, t), []).append(payload)
    return index


def index_get(
    index: dict[tuple[str, str], list[dict[str, Any]]],
    artist: str,
    title: str,
) -> list[dict[str, Any]]:
    """Return the deduped payloads for any matching artist/title variant pair.

    A payload is reachable through several variant pairs, so we dedup by the
    payload's identity (its sorted items) to avoid double-counting versions.
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, Any], ...]] = set()
    for a in artist_variants(artist):
        for t in title_variants(title):
            for payload in index.get((a, t), ()):
                fingerprint = tuple(sorted(payload.items()))
                if fingerprint not in seen:
                    seen.add(fingerprint)
                    out.append(payload)
    return out
