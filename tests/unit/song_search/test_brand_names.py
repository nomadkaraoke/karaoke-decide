from karaoke_decide.song_search.brand_names import KN_BRAND_NAMES, brand_name, brand_name_for
from karaoke_decide.song_search.version_priority import display_name_for


def test_brand_name_known_code():
    assert brand_name("NOMAD") == "Nomad Karaoke"
    assert brand_name(" obsk ") == "ObsKure Karaoke"


def test_brand_name_unknown_code_falls_back_to_code():
    assert brand_name("ZZZ") == "ZZZ"


def test_brand_name_empty():
    assert brand_name("") == ""
    assert brand_name_for("") == ""


def test_gen_brand_names_agree_with_kjbox_display_names():
    for code, name in KN_BRAND_NAMES.items():
        assert display_name_for(code) == name
