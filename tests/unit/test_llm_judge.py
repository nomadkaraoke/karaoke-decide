"""Tests for the Gemini (Developer API) LLM karaoke-suitability judge."""

from unittest.mock import MagicMock, patch

import pytest

from karaoke_decide.core.exceptions import ExternalServiceError
from karaoke_decide.services.llm_judge import LlmJudge, LlmQuotaExhaustedError


def _judge_with_response(text: str) -> LlmJudge:
    j = LlmJudge("gemini-3.8-flash")
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = MagicMock(text=text)
    j._client = fake_client
    return j


class TestJudge:
    def test_keep_verdict(self):
        j = _judge_with_response('{"verdict":"keep","confidence":0.9,"reason":"vocal"}')
        v = j.judge("A", "B", "some lyrics", {"instrumentalness": 0.1})
        assert v.keep is True and v.confidence == 0.9 and v.reason == "vocal"

    def test_reject_verdict(self):
        j = _judge_with_response('{"verdict":"reject","confidence":0.8,"reason":"instr"}')
        v = j.judge("A", "B", "x", {})
        assert v.keep is False and v.reason == "instr"

    def test_failsafe_keeps_on_unknown_verdict(self):
        # Only an explicit "reject" drops a track.
        j = _judge_with_response('{"verdict":"maybe","confidence":0.5,"reason":"?"}')
        assert j.judge("A", "B", "x", {}).keep is True

    def test_non_numeric_confidence_does_not_crash(self):
        j = _judge_with_response('{"verdict":"keep","confidence":"high","reason":"ok"}')
        v = j.judge("A", "B", "x", {})
        assert v.keep is True and v.confidence == 0.0

    def test_tolerates_code_fences(self):
        j = _judge_with_response('```json\n{"verdict":"reject","reason":"r"}\n```')
        assert j.judge("A", "B", "x", {}).keep is False

    def test_empty_response_raises(self):
        j = _judge_with_response("")
        with pytest.raises(ExternalServiceError):
            j.judge("A", "B", "x", {})

    def test_bad_json_raises(self):
        j = _judge_with_response("not json at all")
        with pytest.raises(ExternalServiceError):
            j.judge("A", "B", "x", {})

    def test_sdk_error_wrapped(self):
        j = LlmJudge("m")
        fake = MagicMock()
        fake.models.generate_content.side_effect = RuntimeError("gemini down")
        j._client = fake
        with pytest.raises(ExternalServiceError):
            j.judge("A", "B", "x", {})

    def test_sdk_error_is_not_quota(self):
        j = LlmJudge("m")
        fake = MagicMock()
        fake.models.generate_content.side_effect = RuntimeError("503 UNAVAILABLE overloaded")
        j._client = fake
        with pytest.raises(ExternalServiceError) as ei:
            j.judge("A", "B", "x", {})
        assert not isinstance(ei.value, LlmQuotaExhaustedError)

    def test_quota_error_raises_quota_exhausted(self):
        class FakeAPIError(Exception):
            code = 429
            status = "RESOURCE_EXHAUSTED"

        j = LlmJudge("m")
        fake = MagicMock()
        fake.models.generate_content.side_effect = FakeAPIError("quota")
        j._client = fake
        with pytest.raises(LlmQuotaExhaustedError):
            j.judge("A", "B", "x", {})

    def test_client_uses_api_key_not_vertex(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        with patch("google.genai.Client") as client_cls:
            LlmJudge("m")._get_client()
        kwargs = client_cls.call_args.kwargs
        assert kwargs["api_key"] == "test-key"
        assert "vertexai" not in kwargs and "project" not in kwargs
