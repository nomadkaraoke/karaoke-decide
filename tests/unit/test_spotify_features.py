"""Tests for the Spotify audio-features lookup service."""

from unittest.mock import MagicMock

from karaoke_decide.services.spotify_features import SpotifyFeaturesService


def _row(na, nt, inst=0.1, dur=240000, pop=30):
    return {
        "na": na,
        "nt": nt,
        "instrumentalness": inst,
        "speechiness": 0.05,
        "energy": 0.8,
        "valence": 0.5,
        "danceability": 0.6,
        "tempo": 174.0,
        "duration_ms": dur,
        "popularity": pop,
        "explicit": 0,
    }


def _service(rows):
    client = MagicMock()
    client.query.return_value.result.return_value = rows
    return SpotifyFeaturesService(client=client)


class TestLookup:
    def test_matches_by_normalized_name(self):
        svc = _service([_row("pendulum", "slam", inst=0.4, dur=210000)])
        out = svc.lookup([("Pendulum", "Slam")])
        assert ("Pendulum", "Slam") in out
        assert out[("Pendulum", "Slam")].instrumentalness == 0.4
        assert out[("Pendulum", "Slam")].duration_ms == 210000

    def test_unmatched_absent(self):
        svc = _service([])
        assert svc.lookup([("Nobody", "Nothing")]) == {}

    def test_matches_after_stripping_decorations(self):
        # "Ghost Assassin - Original Mix" should match spotify "ghostassassin".
        svc = _service([_row("maduk", "ghostassassin")])
        out = svc.lookup([("Maduk", "Ghost Assassin - Original Mix")])
        assert ("Maduk", "Ghost Assassin - Original Mix") in out

    def test_empty_input_no_query(self):
        client = MagicMock()
        svc = SpotifyFeaturesService(client=client)
        assert svc.lookup([]) == {}
        client.query.assert_not_called()

    def test_the_prefix_variant(self):
        svc = _service([_row("prodigy", "firestarter")])
        out = svc.lookup([("The Prodigy", "Firestarter")])
        assert ("The Prodigy", "Firestarter") in out
