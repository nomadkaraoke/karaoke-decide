# Vendored from kjbox kj-controller/sing.py @ 8973e4b; keep in step (parity tests in tests/unit/song_search/).
"""How an identified song relates to what was typed (kjbox ``sing._identify_kind``).

Drives the client's banner: nothing for ``same``, "Tidied to …" for ``cosmetic``,
the filled-in song for ``completed``, "Corrected to … — you typed …" for ``content``.
"""

from __future__ import annotations

from typing import Literal

from karaoke_decide.song_search.identify import song_norm

IdentifyKind = Literal["same", "cosmetic", "completed", "content"]


def _compact(s: str) -> str:
    return song_norm(s).replace(" ", "")


def identify_kind(query: str, artist: str, title: str) -> IdentifyKind:
    """How the identified song relates to what was typed.

    ``same``      typed exactly (nothing to announce)
    ``cosmetic``  the same words, only casing/punctuation/order differ → "Tidied to"
    ``completed`` the typed words are the title (or part of artist + title) and
                  identification added what was missing (usually the artist)
    ``content``   the typed words differ (typos etc.) → "Corrected to"
    """
    typed = _compact(query)
    full = {_compact(f"{artist} {title}"), _compact(f"{title} {artist}")}
    if query.strip() in (f"{artist} {title}", f"{title} {artist}", f"{artist} - {title}", f"{title} - {artist}"):
        return "same"
    if typed in full:
        return "cosmetic"
    words = set(song_norm(query).split())
    song_words = set(song_norm(f"{artist} {title}").split())
    if words and words <= song_words:
        return "completed"
    return "content"
