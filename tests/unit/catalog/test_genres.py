from karaoke_decide.catalog.genres import GENRE_IDS, map_genres


def test_genre_ids_are_the_22_quiz_ids() -> None:
    assert len(GENRE_IDS) == 22
    assert GENRE_IDS[-1] == "other"
    assert "classic-rock" in GENRE_IDS and "kpop" in GENRE_IDS


def test_classic_rock_matches_both_longest_first() -> None:
    assert map_genres(["classic rock"]) == ["classic-rock", "rock"]


def test_kpop_matches_kpop_and_pop() -> None:
    assert map_genres(["k-pop"]) == ["kpop", "pop"]


def test_unknown_genre_maps_to_nothing() -> None:
    assert map_genres(["polka"]) == []


def test_dedupes_across_inputs_and_keeps_first_seen_order() -> None:
    assert map_genres(["indie rock", "alternative rock"]) == ["indie", "rock"]


def test_all_mapped_ids_are_valid() -> None:
    raw = ["hip hop", "r&b", "electro", "reggaeton", "broadway", "soul", "funk", "dance pop", "emo", "ska punk"]
    assert set(map_genres(raw)) <= set(GENRE_IDS)


def test_empty_input() -> None:
    assert map_genres([]) == []


def test_reggaeton_is_latin_only() -> None:
    assert map_genres(["reggaeton"]) == ["latin"]


def test_reggae_still_maps() -> None:
    assert map_genres(["roots reggae"]) == ["reggae"]


def test_dance_pop_and_ska_punk_exact() -> None:
    assert map_genres(["dance pop"]) == ["electronic", "pop"]
    assert map_genres(["ska punk"]) == ["punk", "ska"]


def test_lowercases_input() -> None:
    assert map_genres(["Classic Rock"]) == ["classic-rock", "rock"]
