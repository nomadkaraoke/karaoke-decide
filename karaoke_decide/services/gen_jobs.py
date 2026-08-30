"""karaoke-gen job lookup — the cheap "already ours" eliminator.

karaoke-gen and karaoke-decide share the same GCP project and Firestore
instance, so we can read gen's ``jobs`` collection directly. Its purpose here
is to drop, before any network call, any song we've already produced (or are
mid-way through producing) — that's the cheapest possible way to know a song
already has a Nomad karaoke version, without querying KaraokeNerds.

We query this FRESH every run (no cache) so the candidate list updates the
moment a job finishes. A job in any non-failed/non-cancelled state eliminates
the song; only failed/cancelled attempts stay eligible to try again.
"""

from __future__ import annotations

from google.cloud import firestore

from karaoke_decide.candidates.matching import canonical_key

# Statuses that mean "we already have / are making this" -> eliminate.
# Everything NOT in this set (failed, cancelled) stays eligible.
_ELIGIBLE_AGAIN_STATUSES = {"failed", "cancelled"}


class GenJobsService:
    """Reads the shared karaoke-gen ``jobs`` collection."""

    def __init__(
        self,
        client: firestore.Client | None = None,
        project: str = "nomadkaraoke",
        collection: str = "jobs",
    ):
        self._client = client
        self.project = project
        self.collection = collection

    @property
    def client(self) -> firestore.Client:
        if self._client is None:
            self._client = firestore.Client(project=self.project)
        return self._client

    def produced_keys(self) -> set[tuple[str, str]]:
        """Canonical (artist, title) keys for every non-failed/cancelled job."""
        keys: set[tuple[str, str]] = set()
        query = self.client.collection(self.collection).select(["artist", "title", "status"])
        for doc in query.stream():
            data = doc.to_dict() or {}
            status = (data.get("status") or "").lower()
            if status in _ELIGIBLE_AGAIN_STATUSES:
                continue
            artist = data.get("artist") or ""
            title = data.get("title") or ""
            if artist and title:
                keys.add(canonical_key(artist, title))
        return keys
