"""v2 filters and sorts: every filter is SQL against catalog.db (+ av)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from backend.services.recs_v2.filters import LIBRARY_CODES, ORDER_SQL, RecFilters, filters_applied, where_sql
from karaoke_decide.catalog.moods import MOODS
from karaoke_decide.catalog.reader import CatalogReader


def _keys(reader: CatalogReader, f: RecFilters, order: str = "s.song_key") -> list[str]:
    where, params = where_sql(f, has_availability=reader.has_availability)
    sql = f"SELECT s.song_key FROM songs s WHERE {where} ORDER BY {order}"
    return [r[0] for r in reader.conn().execute(sql, params)]


CASES: list[dict[str, Any]] = [
    {"genre": ["rock"]},
    {"mood": ["sad"]},
    {"energy": "high"},
    {"decade": ["1980s"]},
    {"popularity": 70},
    {"popularity_max": 30},
    {"lead_gender": "female"},
    {"duration": "long"},
    {"classic": True},
    {"explicit": "exclude"},
    {"has_karaoke": "make"},
    {"has_karaoke": "yes"},
    {"library": "community"},
    {"library": "karafun"},
]


@pytest.mark.parametrize("kwargs", CASES, ids=lambda k: str(k))
def test_each_filter_changes_result_set(reader: CatalogReader, kwargs: dict[str, Any]) -> None:
    total = len(_keys(reader, RecFilters()))
    got = _keys(reader, RecFilters.model_validate(kwargs))
    assert 0 < len(got) < total


def test_default_filters_are_a_noop(reader: CatalogReader) -> None:
    assert where_sql(RecFilters()) == ("1", [])
    assert filters_applied(RecFilters()) == {}


def test_karafun_is_kv(reader: CatalogReader) -> None:
    assert LIBRARY_CODES["karafun"] == "KV"
    keys = _keys(reader, RecFilters(library="karafun"))
    assert keys
    c = reader.conn()
    for k in keys:
        assert c.execute("SELECT 1 FROM av.song_libraries WHERE song_key=? AND library_code='KV'", (k,)).fetchone()


def test_community_has_community_version(reader: CatalogReader) -> None:
    c = reader.conn()
    for k in _keys(reader, RecFilters(library="community")):
        assert c.execute("SELECT 1 FROM av.versions WHERE song_key=? AND kind='community'", (k,)).fetchone()


def test_has_karaoke_partitions_catalog(reader: CatalogReader) -> None:
    yes = set(_keys(reader, RecFilters(has_karaoke="yes")))
    make = set(_keys(reader, RecFilters(has_karaoke="make")))
    assert not yes & make
    assert yes | make == set(_keys(reader, RecFilters()))


def test_field_semantics(reader: CatalogReader) -> None:
    c = reader.conn()

    def rows(f: RecFilters, cols: str) -> list[Any]:
        where, params = where_sql(f)
        return list(c.execute(f"SELECT {cols} FROM songs s WHERE {where}", params))

    assert all(r[0] >= 70 for r in rows(RecFilters(popularity=70), "popularity"))
    assert all(r[0] <= 30 for r in rows(RecFilters(popularity_max=30), "popularity"))
    assert all(r[0] > 300_000 for r in rows(RecFilters(duration="long"), "duration_ms"))
    assert all(r[0] < 180_000 for r in rows(RecFilters(duration="short"), "duration_ms"))
    assert all(180_000 <= r[0] <= 300_000 for r in rows(RecFilters(duration="medium"), "duration_ms"))
    assert all(r[0] >= 0.7 for r in rows(RecFilters(energy="high"), "energy"))
    assert all(r[0] == 0 for r in rows(RecFilters(explicit="exclude"), "explicit"))
    assert all(r[0] == 1 for r in rows(RecFilters(classic=True), "is_classic"))
    assert {r[0] for r in rows(RecFilters(decade=["1980s", "1990s"]), "decade")} <= {"1980s", "1990s"}
    assert {r[0] for r in rows(RecFilters(mood=["sad", "happy"]), "mood")} <= {"sad", "happy"}
    both = set(_keys(reader, RecFilters(genre=["rock", "pop"])))
    assert both > set(_keys(reader, RecFilters(genre=["rock"])))


def test_filters_combine_with_and(reader: CatalogReader) -> None:
    a = set(_keys(reader, RecFilters(energy="high")))
    b = set(_keys(reader, RecFilters(decade=["1980s"])))
    assert set(_keys(reader, RecFilters(energy="high", decade=["1980s"]))) == a & b


def test_without_availability_falls_back_to_in_kn(reader_no_av: CatalogReader) -> None:
    kn = {r[0] for r in reader_no_av.conn().execute("SELECT song_key FROM songs WHERE in_kn = 1")}
    for kwargs, expected in [
        ({"has_karaoke": "yes"}, kn),
        ({"library": "community"}, kn),
        ({"library": "karafun"}, kn),
    ]:
        assert set(_keys(reader_no_av, RecFilters.model_validate(kwargs))) == expected
    make = set(_keys(reader_no_av, RecFilters(has_karaoke="make")))
    assert make and not make & kn
    assert filters_applied(RecFilters(library="karafun"), has_availability=False)["availability"] == "approximate"
    assert "availability" not in filters_applied(RecFilters(energy="low"), has_availability=False)
    assert "availability" not in filters_applied(RecFilters(library="karafun"), has_availability=True)


def test_sorts(reader: CatalogReader) -> None:
    c = reader.conn()

    def ordered(sort: str, cols: str) -> list[Any]:
        return list(c.execute(f"SELECT {cols} FROM songs s ORDER BY {ORDER_SQL[sort]}"))  # type: ignore[index]

    years = [r[0] for r in ordered("newest", "year, s.song_key") if r[0] is not None]
    assert years == sorted(years, reverse=True)
    old = [r[0] for r in ordered("oldest", "year, s.song_key") if r[0] is not None]
    assert old == sorted(old)
    durs = [r[0] for r in ordered("shortest", "duration_ms, s.song_key") if r[0] is not None]
    assert durs == sorted(durs)
    pops = [r[0] for r in ordered("popular", "popularity, s.song_key") if r[0] is not None]
    assert pops == sorted(pops, reverse=True)
    titles = [r[0].lower() for r in ordered("az", "title, s.song_key")]
    assert titles == sorted(titles)
    # Ties are broken by song_key, so the order is fully deterministic.
    rows = ordered("newest", "year, s.song_key")
    same_year = [r[1] for r in rows if r[0] == rows[0][0]]
    assert same_year == sorted(same_year)
    assert "best" not in ORDER_SQL
    assert all(v.endswith(", s.song_key") for v in ORDER_SQL.values())


def test_unknown_values_rejected() -> None:
    with pytest.raises(ValidationError):
        RecFilters(mood=["romantic"])  # type: ignore[list-item]
    with pytest.raises(ValidationError):
        RecFilters(library="spotify")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        RecFilters(popularity=101)


def test_mood_literal_matches_catalog_moods() -> None:
    field = RecFilters.model_fields["mood"].annotation
    assert set(field.__args__[0].__args__) == set(MOODS)  # type: ignore[union-attr]


def test_filters_applied_only_non_defaults() -> None:
    f = RecFilters(genre=["rock"], energy="low", classic=True, explicit="exclude", library="community")
    assert filters_applied(f) == {
        "genre": ["rock"],
        "energy": "low",
        "classic": True,
        "explicit": "exclude",
        "library": "community",
    }


def test_injection_is_parameterised(reader: CatalogReader) -> None:
    f = RecFilters(genre=["x'; DROP TABLE songs; --"], decade=["' OR 1=1 --"])
    where, params = where_sql(f)
    assert "DROP" not in where and "OR 1=1" not in where
    assert _keys(reader, f) == []
    assert reader.conn().execute("SELECT count(*) FROM songs").fetchone()[0] > 0
    with pytest.raises(ValueError):
        where_sql(RecFilters(), alias="s; DROP")
