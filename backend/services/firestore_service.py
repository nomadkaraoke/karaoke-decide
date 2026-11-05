"""Firestore database service."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any, TypeVar

from backend.config import BackendSettings
from karaoke_decide.core.lazy_import import lazy_module

if TYPE_CHECKING:
    from google.api_core import exceptions as api_exceptions
    from google.cloud import firestore
else:  # imported on first use (cold start: grpc + protobuf + firestore are ~1/4 of app import)
    api_exceptions = lazy_module("google.api_core.exceptions")
    firestore = lazy_module("google.cloud.firestore")


logger = logging.getLogger(__name__)

_T = TypeVar("_T")

WriteListener = Callable[[str, Sequence[str]], None]
_write_listeners: list[WriteListener] = []

USER_SONGS = "user_songs"
# A heavy user's snapshot meta doc (backend/services/user_songs_snapshot.py), keyed by user id.
USER_SONGS_SNAPSHOT_META = "user_songs_snapshots"
# A write of more ids than this marks the snapshot stale, rather than listing them.
SNAPSHOT_BULK_WRITE_IDS = 50


def add_write_listener(listener: WriteListener) -> None:
    """``listener(collection, doc_ids)`` runs after each committed write through ``FirestoreService``."""
    _write_listeners.append(listener)


def _notify_write(collection: str, doc_ids: Sequence[str]) -> None:
    for listener in _write_listeners:
        listener(collection, doc_ids)


class TransactionView:
    """Document reads and writes inside one Firestore transaction.

    Firestore requires every read to happen before the first write.
    """

    def __init__(
        self,
        service: FirestoreService,
        transaction: firestore.AsyncTransaction,
        written: list[tuple[str, str]] | None = None,
    ) -> None:
        self._service = service
        self._transaction = transaction
        self._written = written if written is not None else []

    async def get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        snap = await self._service.collection(collection).document(doc_id).get(transaction=self._transaction)
        return (snap.to_dict() or {}) if snap.exists else None

    def set(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
        self._transaction.set(self._service.collection(collection).document(doc_id), data)
        self._written.append((collection, doc_id))

    def delete(self, collection: str, doc_id: str) -> None:
        self._transaction.delete(self._service.collection(collection).document(doc_id))
        self._written.append((collection, doc_id))


class FirestoreService:
    """Service for Firestore database operations."""

    def __init__(self, settings: BackendSettings):
        self.settings = settings
        self._client: firestore.AsyncClient | None = None

    @property
    def client(self) -> firestore.AsyncClient:
        """Get or create Firestore client."""
        if self._client is None:
            self._client = firestore.AsyncClient(
                project=self.settings.google_cloud_project,
                database=self.settings.firestore_database,
            )
        return self._client

    def collection(self, name: str) -> firestore.AsyncCollectionReference:
        """Get a collection reference."""
        return self.client.collection(name)

    async def _committed(self, collection: str, doc_ids: Sequence[str]) -> None:
        """After every commit. Awaited, so a reader on any instance sees a writer's own write."""
        _notify_write(collection, doc_ids)
        if collection == USER_SONGS:
            await self._mark_user_songs_snapshot(doc_ids)

    async def _mark_user_songs_snapshot(self, doc_ids: Sequence[str]) -> None:
        """Report written rows to their users' snapshot meta docs. Doc ids are ``{user_id}:...``.

        Users without a snapshot (nearly all) have no meta doc, and the update is a no-op. A failure
        never fails the write that already committed; the snapshot's age limit bounds what it misses."""
        by_user: dict[str, list[str]] = {}
        for doc_id in doc_ids:
            by_user.setdefault(doc_id.split(":", 1)[0], []).append(doc_id)

        async def mark(user_id: str, ids: list[str]) -> None:
            ref = self.collection(USER_SONGS_SNAPSHOT_META).document(user_id)
            bulk = {"rev": firestore.Increment(1), "stale": True, "dirty_ids": []}
            try:
                if len(ids) > SNAPSHOT_BULK_WRITE_IDS:
                    await ref.update(bulk)
                    return
                try:
                    await ref.update({"rev": firestore.Increment(1), "dirty_ids": firestore.ArrayUnion(ids)})
                except api_exceptions.InvalidArgument:  # the list reached the document size limit
                    await ref.update(bulk)
            except api_exceptions.NotFound:
                pass
            except Exception:
                logger.warning("user_songs snapshot marker failed for %s", user_id, exc_info=True)

        await asyncio.gather(*(mark(u, ids) for u, ids in by_user.items()))

    async def get_document(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        """Get a document by ID."""
        doc_ref = self.collection(collection).document(doc_id)
        doc = await doc_ref.get()
        if doc.exists:
            data = doc.to_dict() or {}
            return {"id": doc.id, **data}
        return None

    async def set_document(
        self,
        collection: str,
        doc_id: str,
        data: dict[str, Any],
        merge: bool = False,
    ) -> None:
        """Set a document (create or overwrite)."""
        doc_ref = self.collection(collection).document(doc_id)
        await doc_ref.set(data, merge=merge)
        await self._committed(collection, [doc_id])

    async def create_document(self, collection: str, doc_id: str, data: dict[str, Any]) -> bool:
        """Create a document only if absent. Returns False if it already exists."""
        doc_ref = self.collection(collection).document(doc_id)
        try:
            await doc_ref.create(data)
        except api_exceptions.AlreadyExists:
            return False
        await self._committed(collection, [doc_id])
        return True

    async def batch_set(
        self,
        collection: str,
        docs: dict[str, dict[str, Any]],
        merge: bool = False,
    ) -> None:
        """Write many documents, committing in batches of at most 400."""
        items = list(docs.items())
        for i in range(0, len(items), 400):
            batch = self.client.batch()
            chunk = items[i : i + 400]
            for doc_id, data in chunk:
                batch.set(self.collection(collection).document(doc_id), data, merge=merge)
            await batch.commit()
            await self._committed(collection, [doc_id for doc_id, _ in chunk])

    async def batch_delete(self, collection: str, doc_ids: list[str]) -> None:
        """Delete many documents, committing in batches of at most 400."""
        for i in range(0, len(doc_ids), 400):
            batch = self.client.batch()
            chunk = doc_ids[i : i + 400]
            for doc_id in chunk:
                batch.delete(self.collection(collection).document(doc_id))
            await batch.commit()
            await self._committed(collection, chunk)

    async def get_many(self, collection: str, ids: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch many documents, one ``get_all`` round trip per 300 ids. Missing ids are omitted."""
        found: dict[str, dict[str, Any]] = {}
        for i in range(0, len(ids), 300):
            refs = [self.collection(collection).document(doc_id) for doc_id in ids[i : i + 300]]
            async for snap in self.client.get_all(refs):
                if snap.exists:
                    found[snap.id] = snap.to_dict() or {}
        return found

    async def update_document(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
        """Update specific fields in a document."""
        doc_ref = self.collection(collection).document(doc_id)
        await doc_ref.update(data)
        await self._committed(collection, [doc_id])

    async def delete_document(self, collection: str, doc_id: str) -> None:
        """Delete a document."""
        doc_ref = self.collection(collection).document(doc_id)
        await doc_ref.delete()
        await self._committed(collection, [doc_id])

    async def query_documents(
        self,
        collection: str,
        filters: list[tuple[str, str, Any]] | None = None,
        order_by: str | None = None,
        order_direction: str = "ASCENDING",
        limit: int | None = None,
        offset: int | None = None,
        select: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Query documents with filters.

        Args:
            collection: Collection name
            filters: List of (field, operator, value) tuples
            order_by: Field to order by
            order_direction: ASCENDING or DESCENDING
            limit: Max documents to return
            offset: Number of documents to skip
            select: Only these fields (a smaller response and fewer objects for a big read)

        Returns:
            List of document dictionaries with IDs
        """
        query: firestore.AsyncCollectionReference | firestore.AsyncQuery = self.collection(collection)

        if filters:
            for field, op, value in filters:
                query = query.where(filter=firestore.FieldFilter(field, op, value))

        if order_by:
            direction = firestore.Query.DESCENDING if order_direction == "DESCENDING" else firestore.Query.ASCENDING
            query = query.order_by(order_by, direction=direction)

        if offset:
            query = query.offset(offset)

        if limit:
            query = query.limit(limit)

        if select:
            query = query.select(select)

        docs = []
        async for doc in query.stream():
            data = doc.to_dict() or {}
            docs.append({"id": doc.id, **data})

        return docs

    async def count_documents(
        self,
        collection: str,
        filters: list[tuple[str, str, Any]] | None = None,
    ) -> int:
        """Count documents matching filters."""
        query: firestore.AsyncCollectionReference | firestore.AsyncQuery = self.collection(collection)

        if filters:
            for field, op, value in filters:
                query = query.where(filter=firestore.FieldFilter(field, op, value))

        # Use count aggregation
        count_query = query.count()
        result = await count_query.get()  # type: ignore[call-arg]
        return int(result[0][0].value)

    async def delete_document_atomically(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        """Atomically delete a document and return its contents.

        Uses a Firestore transaction to ensure the document is only
        consumed once, even with concurrent requests.

        Args:
            collection: Collection name
            doc_id: Document ID to delete

        Returns:
            Document data if it existed and was deleted, None otherwise.
        """

        @firestore.async_transactional
        async def delete_in_transaction(
            transaction: firestore.AsyncTransaction,
        ) -> dict[str, Any] | None:
            doc_ref = self.collection(collection).document(doc_id)
            doc = await doc_ref.get(transaction=transaction)

            if not doc.exists:
                return None

            # Delete within transaction
            transaction.delete(doc_ref)
            data = doc.to_dict() or {}
            return {"id": doc.id, **data}

        transaction = self.client.transaction()
        result: dict[str, Any] | None = await delete_in_transaction(transaction)
        await self._committed(collection, [doc_id])
        return result

    async def transform_document_atomically(
        self,
        collection: str,
        doc_id: str,
        transform: Callable[[dict[str, Any] | None], dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        """Atomically read a document, compute its new contents, and write them.

        ``transform`` gets the current data (None if missing) and returns the new
        data to store, or None to delete the document. It may raise to abort
        without writing. It must be side-effect free: Firestore retries the
        transaction (and so calls ``transform`` again) on contention.

        Returns:
            The data returned by ``transform``.
        """

        @firestore.async_transactional
        async def transform_in_transaction(
            transaction: firestore.AsyncTransaction,
        ) -> dict[str, Any] | None:
            doc_ref = self.collection(collection).document(doc_id)
            doc = await doc_ref.get(transaction=transaction)
            new_data = transform((doc.to_dict() or {}) if doc.exists else None)
            if new_data is None:
                if doc.exists:
                    transaction.delete(doc_ref)
            else:
                transaction.set(doc_ref, new_data)
            return new_data

        transaction = self.client.transaction()
        result: dict[str, Any] | None = await transform_in_transaction(transaction)
        await self._committed(collection, [doc_id])
        return result

    async def run_transaction(self, fn: Callable[[TransactionView], Awaitable[_T]]) -> _T:
        """Run ``fn`` in a Firestore transaction spanning any number of documents.

        ``fn`` must do all its reads before its first write and be free of other side
        effects: Firestore retries the whole transaction (calling ``fn`` again) on contention.
        """

        written: list[tuple[str, str]] = []

        @firestore.async_transactional
        async def in_transaction(transaction: firestore.AsyncTransaction) -> _T:
            return await fn(TransactionView(self, transaction, written))

        result: _T = await in_transaction(self.client.transaction())
        # After the commit: reported any earlier, a re-read could cache the rows from before this write.
        for collection, doc_id in written:
            _notify_write(collection, [doc_id])
        songs = [doc_id for collection, doc_id in written if collection == USER_SONGS]
        if songs:
            await self._mark_user_songs_snapshot(songs)
        return result
