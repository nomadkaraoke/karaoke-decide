"""Tests for the karaoke-suitability score."""

from karaoke_decide.candidates.lyrics import analyze
from karaoke_decide.candidates.scoring import ScoreWeights, suitability
from karaoke_decide.services.spotify_features import SpotifyFeatures


def _feat(instrumentalness=0.0, duration_min=4.0):
    return SpotifyFeatures(
        instrumentalness=instrumentalness,
        speechiness=0.05,
        energy=0.8,
        valence=0.5,
        danceability=0.6,
        tempo=174.0,
        duration_ms=int(duration_min * 60000),
        popularity=30,
        explicit=False,
    )


def _rich(n=15):
    return analyze("\n".join(f"line number {i} words here now" for i in range(n)))


class TestSuitability:
    def test_vocal_track_scores_high(self):
        s = suitability(_feat(instrumentalness=0.0), _rich(15))
        assert s.score > 80

    def test_instrumental_track_scores_low(self):
        s = suitability(_feat(instrumentalness=0.95), analyze("one line"))
        assert s.score < 45

    def test_instrumentalness_dominates(self):
        hi = suitability(_feat(instrumentalness=0.1), _rich(15)).score
        lo = suitability(_feat(instrumentalness=0.9), _rich(15)).score
        assert hi > lo

    def test_long_duration_penalized(self):
        short = suitability(_feat(duration_min=4.0), _rich(15)).score
        long = suitability(_feat(duration_min=10.0), _rich(15)).score
        assert short > long

    def test_duration_free_below_threshold(self):
        s6 = suitability(_feat(duration_min=6.0), _rich(15))
        s5 = suitability(_feat(duration_min=5.0), _rich(15))
        assert s6.duration_term == s5.duration_term == 1.0

    def test_breakdown_terms_present(self):
        d = suitability(_feat(), _rich(15)).as_dict()
        assert {"score", "vocal_term", "richness_term", "duration_term"} <= d.keys()

    def test_custom_weights(self):
        w = ScoreWeights(vocal=1.0, richness=0.0, duration=0.0)
        s = suitability(_feat(instrumentalness=0.25), _rich(1), weights=w)
        assert round(s.score, 0) == 75  # purely (1 - 0.25) * 100
