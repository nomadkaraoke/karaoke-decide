from karaoke_decide.vocal_range.merge import (
    SourceRow,
    artist_ranges,
    merge_songs,
)


def row(
    key="s1",
    source="gen_measured",
    lo=50,
    hi=70,
    p50=60.0,
    weight=60.0,
    part="all",
    conf=0.9,
    pop=None,
    artist="A",
    **kw,
):
    return SourceRow(
        song_key=key,
        source=source,
        source_id=kw.get("sid", f"{source}-{key}"),
        artist=artist,
        title="T",
        low_midi=lo,
        high_midi=hi,
        p10=p50 - 5,
        p50=p50,
        p90=p50 + 5,
        octave_confidence=None if source == "gemini" else conf,
        lead_part=part,
        weight_s=weight,
        pop_rank=pop,
        confidence=conf if source == "gemini" else None,
    )


ALL = lambda r: True  # noqa: E731


def test_measured_beats_notes_beats_estimated():
    rows = [row(source="gemini", lo=1), row(source="ultrastar", lo=2), row(source="gen_measured", lo=3)]
    (m,) = merge_songs(rows, ALL)
    assert (m.source, m.tier, m.low_midi, m.n_sources) == ("gen_measured", "measured", 3, 3)
    (m,) = merge_songs(rows[:2], ALL)
    assert (m.source, m.tier) == ("ultrastar", "notes")
    (m,) = merge_songs(rows[:1], ALL)
    assert (m.source, m.tier) == ("gemini", "estimated")


def test_gen_beats_nomad():
    (m,) = merge_songs([row(source="nomad_measured", lo=1), row(source="gen_measured", lo=2)], ALL)
    assert m.source == "gen_measured"


def test_short_measured_ignored():
    rows = [row(source="gen_measured", weight=19.9), row(source="ultrastar", lo=2, weight=100)]
    (m,) = merge_songs(rows, ALL)
    assert m.source == "ultrastar"
    assert merge_songs([row(source="gen_measured", weight=19.9)], ALL) == []


def test_duet_picks_longest_part():
    rows = [row(part="P1", lo=40, weight=30), row(part="P2", lo=45, weight=50)]
    (m,) = merge_songs(rows, ALL)
    assert m.is_duet and m.low_midi == 45
    (solo,) = merge_songs([row(part="all")], ALL)
    assert not solo.is_duet


def test_gemini_dropped_when_band_not_kept():
    g = row(source="gemini", pop=100)
    assert merge_songs([g], lambda r: False) == []
    assert len(merge_songs([g], ALL)) == 1


def test_gemini_low_confidence_dropped():
    assert merge_songs([row(source="gemini", conf=0.29)], ALL) == []
    assert merge_songs([row(source="gemini", conf=None)], ALL) == []


def test_ultrastar_duplicates_choose_median():
    rows = [row(source="ultrastar", p50=p, sid=f"u{p}", weight=50) for p in (50.0, 58.0, 60.0, 80.0)]
    (m,) = merge_songs(rows, ALL)
    assert m.p50 == 58.0  # median 59, tie 58/60 -> lower


def test_output_sorted_by_song_key():
    out = merge_songs([row(key="b"), row(key="a")], ALL)
    assert [m.song_key for m in out] == ["a", "b"]


def _song(key, p50, tier="measured"):
    from karaoke_decide.vocal_range.merge import MergedSong

    return MergedSong(key, p50 - 8, p50 + 8, p50 - 5, p50, p50 + 5, tier, "x", 0.9, False, 1)


def test_artist_basis_precedence():
    songs = [_song(f"s{i}", 60 + i) for i in range(3)] + [_song("t1", 70)]
    artist_of = {"s0": "a1", "s1": "a1", "s2": "a1", "t1": "a2"}
    g = [
        row(key="a2", source="gemini", p50=65.0, conf=0.8, pop=10),
        row(key="a3", source="gemini", p50=66.0, conf=0.1, pop=10),
    ]
    out = {
        r.artist_key: r
        for r in artist_ranges(
            songs,
            artist_of,
            g,
            lambda r: True,
            {"m3": {"tenor"}, "m4": {"bass"}},
            {"a3": "m3", "a4": "m4"},
            {"a5": "male", "a3": "female"},
        )
    }
    assert out["a1"].basis == "songs" and out["a1"].p50 == 61
    assert out["a2"].basis == "gemini" and out["a2"].p50 == 65.0  # 1 song < 3
    assert out["a3"].basis == "p412"  # gemini conf too low
    assert (out["a3"].low_midi, out["a3"].high_midi, out["a3"].p10, out["a3"].p50, out["a3"].p90) == (
        50,
        70,
        53,
        60.0,
        67,
    )
    assert out["a4"].basis == "p412" and out["a4"].low_midi == 42
    assert out["a5"].basis == "gender"
    assert (out["a5"].low_midi, out["a5"].high_midi, out["a5"].p10, out["a5"].p50, out["a5"].p90) == (
        47,
        67,
        50,
        57,
        64,
    )


def test_mixed_gender_no_prior():
    out = artist_ranges([], {}, [], lambda r: True, {}, {}, {"x": "mixed", "y": "unknown", "z": "female"})
    assert [r.artist_key for r in out] == ["z"]
    assert out[0].low_midi == 54 and out[0].p90 == 71


def test_boundaries_exactly_at_threshold_kept():
    assert len(merge_songs([row(source="gen_measured", weight=20.0)], ALL)) == 1
    (m,) = merge_songs([row(source="gemini", conf=0.3)], ALL)
    assert m.confidence == 0.3 and m.octave_confidence is None


def test_mixed_all_and_p1_is_not_duet():
    (m,) = merge_songs([row(part="all", lo=40, weight=30), row(part="P1", lo=45, weight=50)], ALL)
    assert not m.is_duet and m.low_midi == 45


def test_gemini_only_artist_with_estimated_songs_uses_gemini_basis():
    songs = [_song(f"s{i}", 60, tier="estimated") for i in range(3)]
    artist_of = {f"s{i}": "a1" for i in range(3)}
    g = [row(key="a1", source="gemini", p50=65.0, conf=0.8)]
    (r,) = artist_ranges(songs, artist_of, g, lambda r: True, {}, {}, {})
    assert r.basis == "gemini" and r.confidence == 0.8
    assert artist_ranges(songs, artist_of, g, lambda r: False, {}, {}, {}) == []


def test_notes_songs_count_for_songs_basis():
    songs = [_song(f"s{i}", 60, tier="notes") for i in range(3)]
    (r,) = artist_ranges(songs, {f"s{i}": "a1" for i in range(3)}, [], lambda r: True, {}, {}, {})
    assert r.basis == "songs" and r.confidence is None
