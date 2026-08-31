"""Tests for the Vertex AI LLM karaoke-suitability judge."""

from unittest.mock import MagicMock

import pytest

from karaoke_decide.core.exceptions import ExternalServiceError
from karaoke_decide.services.llm_judge import LlmJudge


def _judge_with_response(text: str) -> LlmJudge:
    j = LlmJudge("proj", "global", "gemini-2.5-flash")
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
        j = LlmJudge("proj", "global", "m")
        fake = MagicMock()
        fake.models.generate_content.side_effect = RuntimeError("vertex down")
        j._client = fake
        with pytest.raises(ExternalServiceError):
            j.judge("A", "B", "x", {})
