"""Tests for the manual reject list."""

from karaoke_decide.candidates.matching import canonical_key
from karaoke_decide.candidates.rejects import RejectList


class TestRejectList:
    def test_add_and_load(self, tmp_path):
        rl = RejectList(tmp_path / "rejects.jsonl")
        rl.add("Pendulum", "Slam", "too repetitive live", "2026-08-30")
        entries = rl.load()
        assert len(entries) == 1
        assert entries[0].artist == "Pendulum"
        assert entries[0].reason == "too repetitive live"

    def test_key_set_uses_canonical_key(self, tmp_path):
        rl = RejectList(tmp_path / "rejects.jsonl")
        rl.add("The Beatles", "Hey Jude - Remaster", "no", "2026-08-30")
        assert canonical_key("Beatles", "Hey Jude") in rl.key_set()

    def test_add_is_idempotent(self, tmp_path):
        rl = RejectList(tmp_path / "rejects.jsonl")
        rl.add("Pendulum", "Slam", "reason1", "2026-08-30")
        rl.add("pendulum", "slam", "reason2", "2026-08-31")
        assert len(rl.load()) == 1

    def test_ignores_comments_and_blank_lines(self, tmp_path):
        path = tmp_path / "rejects.jsonl"
        path.write_text(
            "# a comment\n\n"
            '{"artist":"A","title":"B","reason":"r","date":"d"}\n'
            "not-json\n"
        )
        rl = RejectList(path)
        assert len(rl.load()) == 1

    def test_missing_file_is_empty(self, tmp_path):
        rl = RejectList(tmp_path / "nope.jsonl")
        assert rl.load() == []
        assert rl.key_set() == set()
