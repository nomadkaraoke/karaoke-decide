from karaoke_decide.vocal_range.calibrate import calibrate_artists, calibrate_songs
from karaoke_decide.vocal_range.merge import ArtistRange, MergedSong, SourceRow


def est(key, p50, pop, conf=0.8, lo=None, hi=None):
    return SourceRow(
        key,
        "gemini",
        "g",
        "A",
        "T",
        lo if lo is not None else int(p50) - 8,
        hi if hi is not None else int(p50) + 8,
        p50 - 5,
        p50,
        p50 + 5,
        conf,
        "all",
        0.0,
        pop,
    )


def truth(key, p50, tier="measured", conf=0.9):
    return MergedSong(key, 0, 0, p50 - 5, p50, p50 + 5, tier, "x", conf, False, 1)


def band(stats, name):
    return next(b for b in stats if b.band == name)


def test_band_kept_only_under_3():
    rows, tr = [], {}
    for i in range(30):
        rows.append(est(f"a{i}", 60 + 2.9, 100))
        tr[f"a{i}"] = truth(f"a{i}", 60)
        rows.append(est(f"b{i}", 60 + 3.5, 6000))
        tr[f"b{i}"] = truth(f"b{i}", 60)
    s = calibrate_songs(rows, tr)
    assert band(s, "1-5k").kept and band(s, "1-5k").n == 30
    assert band(s, "5k-20k").kept is False
    assert band(s, "5k-20k").median_err == 3.5


def test_truth_filtering():
    rows = [est(f"a{i}", 60, 100) for i in range(3)]
    tr = {"a0": truth("a0", 60, "estimated"), "a1": truth("a1", 60, "notes", 0.4), "a2": truth("a2", 60, "notes", 0.5)}
    assert band(calibrate_songs(rows, tr), "1-5k").n == 1


def test_small_band_uses_pooled():
    rows, tr = [], {}
    for i in range(30):  # big good band
        rows.append(est(f"a{i}", 61, 100))
        tr[f"a{i}"] = truth(f"a{i}", 60)
    for i in range(5):  # small bad band: own median 10, pooled median still 1
        rows.append(est(f"c{i}", 70, 30000))
        tr[f"c{i}"] = truth(f"c{i}", 60)
    s = calibrate_songs(rows, tr)
    c = band(s, "20k-50k")
    assert c.n == 5 and c.median_err == 10 and c.kept is True
    # pooled bad -> not kept
    rows2 = [est(f"c{i}", 70, 30000) for i in range(5)]
    assert band(calibrate_songs(rows2, {r.song_key: truth(r.song_key, 60) for r in rows2}), "20k-50k").kept is False


def _artist_rows(n, pop, p50=60.0):
    return [est(f"x{pop}_{i}", p50, pop, lo=50, hi=70) for i in range(n)]  # centre 60 -> tenor/alto zone


def test_p412_adjacent_counts():
    rows = _artist_rows(30, 100)  # centre 60 -> tenor (48-72 centre 60)
    adj = {r.song_key: {"alto"} for r in rows}  # tenor~alto adjacent
    assert band(calibrate_artists(rows, adj, {}), "1-1k").kept is True
    far = {r.song_key: {"soprano"} for r in rows}
    assert band(calibrate_artists(rows, far, {}), "1-1k").kept is False


def test_artist_checks_need_n_and_songs_check():
    rows = _artist_rows(30, 100)
    assert band(calibrate_artists(rows, {}, {}), "1-1k").kept is False  # neither
    meas = {r.song_key: ArtistRange(r.song_key, 0, 0, 0, 64.0, 0, "songs") for r in rows}  # err 4
    assert band(calibrate_artists(rows, {}, meas), "1-1k").kept is False
    meas = {r.song_key: ArtistRange(r.song_key, 0, 0, 0, 62.0, 0, "songs") for r in rows}  # err 2
    assert band(calibrate_artists(rows, {}, meas), "1-1k").kept is True
    # both available, p412 passes but songs fails
    ok = {r.song_key: {"tenor"} for r in rows}
    meas = {r.song_key: ArtistRange(r.song_key, 0, 0, 0, 64.0, 0, "songs") for r in rows}
    assert band(calibrate_artists(rows, ok, meas), "1-1k").kept is False
