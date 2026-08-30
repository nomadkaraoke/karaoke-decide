"""Tests for the karaoke-gen jobs 'already ours' lookup."""

from unittest.mock import MagicMock

from karaoke_decide.candidates.matching import canonical_key
from karaoke_decide.services.gen_jobs import GenJobsService


def _doc(artist, title, status):
    doc = MagicMock()
    doc.to_dict.return_value = {"artist": artist, "title": title, "status": status}
    return doc


def _service_with_docs(docs):
    client = MagicMock()
    query = MagicMock()
    query.stream.return_value = docs
    client.collection.return_value.select.return_value = query
    return GenJobsService(client=client)


class TestProducedKeys:
    def test_includes_complete_and_inflight(self):
        svc = _service_with_docs(
            [
                _doc("Pendulum", "Slam", "complete"),
                _doc("Netsky", "Rio", "pending"),
                _doc("Maduk", "Ghost", "in_review"),
            ]
        )
        keys = svc.produced_keys()
        assert canonical_key("Pendulum", "Slam") in keys
        assert canonical_key("Netsky", "Rio") in keys
        assert canonical_key("Maduk", "Ghost") in keys

    def test_excludes_failed_and_cancelled(self):
        svc = _service_with_docs(
            [
                _doc("A", "B", "failed"),
                _doc("C", "D", "cancelled"),
            ]
        )
        assert svc.produced_keys() == set()

    def test_ignores_rows_missing_artist_or_title(self):
        svc = _service_with_docs([_doc("", "B", "complete"), _doc("A", "", "complete")])
        assert svc.produced_keys() == set()

    def test_status_case_insensitive(self):
        svc = _service_with_docs([_doc("A", "B", "FAILED")])
        assert svc.produced_keys() == set()
