"""How the identified song relates to what was typed (kjbox sing.py ``_identify_kind``).

The four kinds are ported from kjbox tests/integration/test_sing_search_identify.py
(``test_identify_kinds``), called directly with the identified song instead of via
the Flask route.
"""

import pytest

from karaoke_decide.song_search.identify_kind import identify_kind


@pytest.mark.parametrize(
    "q,artist,title,kind",
    [
        # kjbox test_identify_kinds
        ("rihanna push up on me", "Rihanna", "Push Up On Me", "cosmetic"),  # same words → "Tidied to"
        ("Rihanna Push Up On Me", "Rihanna", "Push Up On Me", "same"),  # typed exactly → nothing to say
        ("espresso", "Sabrina Carpenter", "Espresso", "completed"),  # title only → artist added
        ("the stokes max picu", "The Strokes", "Machu Picchu", "content"),  # typos → "Corrected to"
        # the other accepted "same" spellings
        ("Push Up On Me Rihanna", "Rihanna", "Push Up On Me", "same"),
        ("Rihanna - Push Up On Me", "Rihanna", "Push Up On Me", "same"),
        ("  Push Up On Me - Rihanna  ", "Rihanna", "Push Up On Me", "same"),
        # punctuation / order only
        ("push up on me, rihanna!", "Rihanna", "Push Up On Me", "cosmetic"),
        ("party in the usa miley cyrus", "Miley Cyrus", "Party in the U.S.A.", "cosmetic"),
        # a subset of the song's words (artist fragment + partial title)
        ("seal kiss", "Seal", "Kiss from a Rose", "completed"),
        ("strokes adults are talking", "The Strokes", "The Adults Are Talking", "completed"),
        # typo / different words
        ("starlite muse", "Muse", "Starlight", "content"),
        ("rihanna breaking dishes", "Rihanna", "Breakin' Dishes", "content"),
        ("", "Muse", "Starlight", "content"),
    ],
)
def test_identify_kind(q: str, artist: str, title: str, kind: str) -> None:
    assert identify_kind(q, artist, title) == kind
