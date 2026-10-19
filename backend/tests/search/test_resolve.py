"""GET /api/search/resolve: the gen Gemini-fallback proxy, with gen mocked via httpx.MockTransport."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.search.models import ResolveResponse
from backend.search.ratelimit import RateLimits
from karaoke_decide.song_search.text_normalize import song_key

STARLIGHT = song_key("Muse", "Starlight")
HEADERS = {"X-Device-Id": "dev-1"}

Handler = Callable[[httpx.Request], httpx.Response]


def _verdict(**kw: Any) -> dict[str, Any]:
    base = {
        "kind": "none",
        "confident": False,
        "typed_artist": "",
        "typed_title": "",
        "canonical_artist": "",
        "canonical_title": "",
        "alternatives": [],
        "engine": "ai",
        "reason": "",
    }
    return {**base, **kw}


class Gen:
    """Scripted gen: records requests, answers with ``handler``."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.handler: Handler = lambda r: httpx.Response(200, json=_verdict())

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    def answer(self, **kw: Any) -> None:
        self.handler = lambda r: httpx.Response(200, json=_verdict(**kw))


@pytest.fixture
def gen() -> Gen:
    return Gen()


def _make_client(
    index_dir: Path, monkeypatch: pytest.MonkeyPatch, gen: Gen, limits: RateLimits | None = None
) -> TestClient:
    from backend.search.app import create_search_app
    from backend.search.resolve import RESOLVE_LIMITS

    monkeypatch.setenv("SONG_INDEX_LOCAL_DIR", str(index_dir))
    monkeypatch.delenv("SONG_INDEX_RUN", raising=False)
    monkeypatch.setenv("GEN_API_URL", "http://gen.test")
    monkeypatch.setenv("DECIDE_GEN_PARTNER_SECRET", "s3cret")
    app = create_search_app(resolve_limits=limits or RESOLVE_LIMITS, gen_transport=httpx.MockTransport(gen))
    return TestClient(app)


@pytest.fixture
def rclient(index_dir: Path, monkeypatch: pytest.MonkeyPatch, gen: Gen) -> Iterator[TestClient]:
    with _make_client(index_dir, monkeypatch, gen) as c:
        yield c


def _get(c: TestClient, q: str, **kw: Any) -> httpx.Response:
    return c.get("/api/search/resolve", params={"q": q}, headers=HEADERS, **kw)


def test_request_to_gen_has_secret_and_body(rclient: TestClient, gen: Gen) -> None:
    gen.answer()
    _get(rclient, "  muse   starlite ")
    (req,) = gen.requests
    assert str(req.url) == "http://gen.test/api/kjbox/catalog/resolve"
    assert req.method == "POST"
    assert req.headers["X-Partner-Secret"] == "s3cret"
    assert json.loads(req.content) == {"query": "muse starlite"}


@pytest.mark.parametrize("kind", ["content", "cosmetic"])
def test_confident_verdict_maps_to_corrected_and_groups(rclient: TestClient, gen: Gen, kind: str) -> None:
    gen.answer(kind=kind, confident=True, canonical_artist="Muse", canonical_title="Starlight", engine="catalog")
    r = _get(rclient, "muse starlite")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == kind
    assert body["typed"] == "muse starlite"
    assert body["corrected"]["artist"] == "Muse"
    assert body["corrected"]["song_key"] == STARLIGHT
    assert body["corrected"]["karaoke"] is True
    assert body["groups"][0]["song_key"] == STARLIGHT
    keys = [g["song_key"] for g in body["groups"]]
    assert len(keys) == len(set(keys))
    assert "split" not in body and "alternatives" not in body


def test_confident_verdict_without_karaoke_still_corrected_with_no_own_group(rclient: TestClient, gen: Gen) -> None:
    gen.answer(kind="content", confident=True, canonical_artist="Zzyzx Nobody", canonical_title="Never Heard Of It")
    body = _get(rclient, "zzyzx").json()
    assert body["corrected"]["karaoke"] is False
    assert body["corrected"]["song_key"] == song_key("Zzyzx Nobody", "Never Heard Of It")
    assert all(g["song_key"] != body["corrected"]["song_key"] for g in body.get("groups", []))


def test_unconfident_verdict_is_empty(rclient: TestClient, gen: Gen) -> None:
    gen.answer(kind="content", confident=False, canonical_artist="Muse", canonical_title="Starlight")
    assert _get(rclient, "muse").json() == {}


def test_ambiguous_maps_to_alternatives(rclient: TestClient, gen: Gen) -> None:
    gen.answer(
        kind="ambiguous",
        alternatives=[{"artist": "Muse", "title": "Starlight"}, {"artist": "Nobody", "title": "Nothing Song"}],
    )
    body = _get(rclient, "starlight").json()
    assert [a["title"] for a in body["alternatives"]] == ["Starlight", "Nothing Song"]
    assert body["alternatives"][0]["karaoke"] is True
    assert body["alternatives"][0]["song_key"] == STARLIGHT
    assert body["alternatives"][1]["karaoke"] is False
    assert "corrected" not in body


def test_none_with_split_maps_to_split(rclient: TestClient, gen: Gen) -> None:
    gen.answer(kind="none", typed_artist="Some Band", typed_title="Some Song", reason="no suggestion")
    assert _get(rclient, "some band some song").json() == {"split": {"artist": "Some Band", "title": "Some Song"}}


def test_none_without_split_is_empty(rclient: TestClient, gen: Gen) -> None:
    gen.answer(kind="none")
    assert _get(rclient, "qwerty zxcv").json() == {}


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: httpx.Response(500, text="boom"),
        lambda r: httpx.Response(503),
        lambda r: httpx.Response(429, json={"detail": "rate_limited"}),
        lambda r: httpx.Response(200, text="not json"),
        lambda r: httpx.Response(200, json=["not", "a", "dict"]),
    ],
)
def test_gen_failure_is_200_empty_and_never_error_logged(
    rclient: TestClient, gen: Gen, handler: Handler, caplog: pytest.LogCaptureFixture
) -> None:
    gen.handler = handler
    with caplog.at_level(logging.DEBUG):
        r = _get(rclient, "muse starlite")
    assert r.status_code == 200
    assert r.json() == {}
    assert not [rec for rec in caplog.records if rec.levelno >= logging.ERROR]
    assert any(rec.levelno == logging.WARNING for rec in caplog.records)


def test_gen_timeout_is_200_empty_and_not_cached(
    rclient: TestClient, gen: Gen, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(r: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=r)

    gen.handler = boom
    with caplog.at_level(logging.DEBUG):
        assert _get(rclient, "muse starlite").json() == {}
    assert not [rec for rec in caplog.records if rec.levelno >= logging.ERROR]
    gen.answer(kind="content", confident=True, canonical_artist="Muse", canonical_title="Starlight")
    assert _get(rclient, "muse starlite").json()["corrected"]["artist"] == "Muse"
    assert len(gen.requests) == 2


def test_verdicts_are_cached_by_normalised_query(rclient: TestClient, gen: Gen) -> None:
    gen.answer(kind="content", confident=True, canonical_artist="Muse", canonical_title="Starlight")
    first = _get(rclient, "Muse  Starlite").json()
    again = _get(rclient, " muse starlite").json()
    assert first["corrected"] == again["corrected"]
    assert len(gen.requests) == 1


def test_none_verdicts_are_cached_too(rclient: TestClient, gen: Gen) -> None:
    gen.answer(kind="none")
    _get(rclient, "qwerty zxcv")
    _get(rclient, "QWERTY zxcv")
    assert len(gen.requests) == 1


def test_unavailable_is_not_cached(rclient: TestClient, gen: Gen) -> None:
    gen.answer(kind="none", reason="unavailable")
    assert _get(rclient, "muse starlite").json() == {}
    gen.answer(kind="content", confident=True, canonical_artist="Muse", canonical_title="Starlight")
    assert _get(rclient, "muse starlite").json()["corrected"]["artist"] == "Muse"
    assert len(gen.requests) == 2


def test_unavailable_with_typed_split_is_not_offered_or_cached(rclient: TestClient, gen: Gen) -> None:
    gen.answer(kind="none", typed_artist="A", typed_title="B", reason="unavailable")
    assert _get(rclient, "a b").json() == {}
    _get(rclient, "a b")
    assert len(gen.requests) == 2


def test_cache_is_lru_capped_at_500(index_dir: Path, monkeypatch: pytest.MonkeyPatch, gen: Gen) -> None:
    gen.answer(kind="none")
    with _make_client(index_dir, monkeypatch, gen, RateLimits(10_000, 10_000, 300)) as c:
        for i in range(501):
            _get(c, f"query number {i}")
        assert len(gen.requests) == 501
        _get(c, "query number 500")  # newest: cached
        assert len(gen.requests) == 501
        _get(c, "query number 0")  # evicted: asks gen again
        assert len(gen.requests) == 502


@pytest.mark.parametrize("q", ["", " ", "a", "   x  "])
def test_short_or_empty_query_never_calls_gen(rclient: TestClient, gen: Gen, q: str) -> None:
    assert _get(rclient, q).json() == {}
    assert gen.requests == []


def test_very_long_and_exotic_queries_are_safe(rclient: TestClient, gen: Gen) -> None:
    gen.answer(kind="none")
    for q in ("x" * 5000, "😀😀😀", "שלום עולם", "夜に駆ける"):
        assert _get(rclient, q).status_code == 200


def test_no_secret_answers_empty_without_calling_gen(
    index_dir: Path, monkeypatch: pytest.MonkeyPatch, gen: Gen, caplog: pytest.LogCaptureFixture
) -> None:
    from backend.search.app import create_search_app

    monkeypatch.setenv("SONG_INDEX_LOCAL_DIR", str(index_dir))
    monkeypatch.setenv("DECIDE_GEN_PARTNER_SECRET", "")
    with caplog.at_level(logging.WARNING):
        with TestClient(create_search_app(gen_transport=httpx.MockTransport(gen))) as c:
            assert _get(c, "muse starlite").json() == {}
            assert _get(c, "muse starlite").json() == {}
    assert gen.requests == []
    warnings = [r for r in caplog.records if "DECIDE_GEN_PARTNER_SECRET" in r.getMessage()]
    assert len(warnings) == 1  # once at startup, not per request


def test_resolve_logs_one_structured_line(rclient: TestClient, gen: Gen, caplog: pytest.LogCaptureFixture) -> None:
    gen.answer(kind="content", confident=True, canonical_artist="Muse", canonical_title="Starlight")
    with caplog.at_level(logging.INFO, logger="search_events"):
        rclient.get("/api/search/resolve", params={"q": "muse starlite", "sid": "abc"}, headers=HEADERS)
    (rec,) = [r for r in caplog.records if r.name == "search_events"]
    fields = rec.json_fields  # type: ignore[attr-defined]
    assert fields["type"] == "resolve"
    assert fields["sid"] == "abc"
    assert fields["q"] == "muse starlite"
    assert fields["kind"] == "content"
    assert isinstance(fields["ms"], float)


def test_rate_limit_per_device_21st_request_is_429(rclient: TestClient, gen: Gen) -> None:
    gen.answer(kind="none")
    for i in range(20):
        assert _get(rclient, f"query number {i}").status_code == 200
    r = _get(rclient, "query number 20")
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1


def test_rate_limit_per_ip_is_200(index_dir: Path, monkeypatch: pytest.MonkeyPatch, gen: Gen) -> None:
    gen.answer(kind="none")
    with _make_client(index_dir, monkeypatch, gen) as c:
        for i in range(200):
            r = c.get(
                "/api/search/resolve",
                params={"q": f"query number {i}"},
                headers={"X-Device-Id": f"dev-{i // 20}", "CF-Connecting-IP": "9.9.9.9"},
            )
            assert r.status_code == 200
        r = c.get(
            "/api/search/resolve",
            params={"q": "one more"},
            headers={"X-Device-Id": "fresh", "CF-Connecting-IP": "9.9.9.9"},
        )
        assert r.status_code == 429


def test_search_limits_do_not_consume_resolve_budget(rclient: TestClient, gen: Gen) -> None:
    gen.answer(kind="none")
    for _ in range(30):
        assert rclient.get("/api/search", params={"q": "muse"}, headers=HEADERS).status_code == 200
    assert _get(rclient, "query number 1").status_code == 200


def test_mapping_failure_is_200_empty_warning_only_and_not_cached(
    rclient: TestClient, gen: Gen, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from backend.search.resolve import GenResolver

    gen.answer(kind="content", confident=True, canonical_artist="Muse", canonical_title="Starlight")
    real = GenResolver._map

    def boom(*a: Any, **k: Any) -> ResolveResponse:
        raise KeyError("versions")

    monkeypatch.setattr(GenResolver, "_map", staticmethod(boom))
    with caplog.at_level(logging.DEBUG):
        r = _get(rclient, "muse starlite")
    assert r.status_code == 200
    assert r.json() == {}
    assert not [rec for rec in caplog.records if rec.levelno >= logging.ERROR]
    assert any(rec.levelno == logging.WARNING for rec in caplog.records)
    monkeypatch.setattr(GenResolver, "_map", staticmethod(real))
    assert _get(rclient, "muse starlite").json()["corrected"]["artist"] == "Muse"
    assert len(gen.requests) == 2


def test_secret_never_leaks_into_logs_or_bodies(
    rclient: TestClient, gen: Gen, caplog: pytest.LogCaptureFixture
) -> None:
    gen.handler = lambda r: httpx.Response(500, text="boom")
    with caplog.at_level(logging.DEBUG):
        r = _get(rclient, "muse starlite")
    assert "s3cret" not in caplog.text
    assert "s3cret" not in r.text


def test_lru_touching_oldest_key_keeps_it(index_dir: Path, monkeypatch: pytest.MonkeyPatch, gen: Gen) -> None:
    gen.answer(kind="none")
    with _make_client(index_dir, monkeypatch, gen, RateLimits(10_000, 10_000, 300)) as c:
        for i in range(500):
            _get(c, f"query number {i}")
        _get(c, "query number 0")  # touch the oldest: now newest
        _get(c, "query number 500")  # evicts number 1, not 0
        n = len(gen.requests)
        _get(c, "query number 0")
        assert len(gen.requests) == n  # survived
        _get(c, "query number 1")
        assert len(gen.requests) == n + 1  # evicted


def test_client_timeout_is_the_constant(rclient: TestClient) -> None:
    from backend.search.resolve import GEN_TIMEOUT_S

    resolver = rclient.app.state.search.resolver  # type: ignore[attr-defined]
    assert resolver._client.timeout == httpx.Timeout(GEN_TIMEOUT_S)


def test_cache_hit_echoes_this_requests_typed_text(rclient: TestClient, gen: Gen) -> None:
    gen.answer(kind="content", confident=True, canonical_artist="Muse", canonical_title="Starlight")
    assert _get(rclient, "Muse Starlite").json()["typed"] == "Muse Starlite"
    assert _get(rclient, "muse STARLITE").json()["typed"] == "muse STARLITE"
    assert len(gen.requests) == 1


def test_nested_nulls_are_preserved_like_search(rclient: TestClient, gen: Gen) -> None:
    gen.answer(kind="content", confident=True, canonical_artist="Muse", canonical_title="Starlight")
    body = _get(rclient, "muse starlite").json()
    assert "popularity" in body["corrected"]
    assert any(v["youtube_url"] is None for v in body["groups"][0]["versions"])
    gen.answer(kind="ambiguous", alternatives=[{"artist": "Nobody", "title": "Nothing Song"}])
    alt = _get(rclient, "nothing").json()["alternatives"][0]
    assert alt["popularity"] is None
