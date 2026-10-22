"""Firestore database service."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeVar

from backend.config import BackendSettings
from karaoke_decide.core.lazy_import import lazy_module

if TYPE_CHECKING:
    from google.api_core import exceptions as api_exceptions
    from google.cloud import firestore
else:  # imported on first use (cold start: grpc + protobuf + firestore are ~1/4 of app import)
    api_exceptions = lazy_module("google.api_core.exceptions")
    firestore = lazy_module("google.cloud.firestore")


_T = TypeVar("_T")


class TransactionView:
    """Document reads and writes inside one Firestore transaction.

    Firestore requires every read to happen before the first write.
    """

    def __init__(self, service: FirestoreService, transaction: firestore.AsyncTransaction) -> None:
        self._service = service
        self._transaction = transaction

    async def get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        snap = await self._service.collection(collection).document(doc_id).get(transaction=self._transaction)
        return (snap.to_dict() or {}) if snap.exists else None

    def set(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
        self._transaction.set(self._service.collection(collection).document(doc_id), data)

    def delete(self, collection: str, doc_id: str) -> None:
        self._transaction.delete(self._service.collection(collection).document(doc_id))


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

    async def create_document(self, collection: str, doc_id: str, data: dict[str, Any]) -> bool:
        """Create a document only if absent. Returns False if it already exists."""
        doc_ref = self.collection(collection).document(doc_id)
        try:
            await doc_ref.create(data)
        except api_exceptions.AlreadyExists:
            return False
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
            for doc_id, data in items[i : i + 400]:
                batch.set(self.collection(collection).document(doc_id), data, merge=merge)
            await batch.commit()

    async def batch_delete(self, collection: str, doc_ids: list[str]) -> None:
        """Delete many documents, committing in batches of at most 400."""
        for i in range(0, len(doc_ids), 400):
            batch = self.client.batch()
            for doc_id in doc_ids[i : i + 400]:
                batch.delete(self.collection(collection).document(doc_id))
            await batch.commit()

    async def get_many(self, collection: str, ids: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch many documents in one round trip. Missing ids are omitted."""
        if not ids:
            return {}
        refs = [self.collection(collection).document(i) for i in ids]
        found: dict[str, dict[str, Any]] = {}
        async for snap in self.client.get_all(refs):
            if snap.exists:
                found[snap.id] = snap.to_dict() or {}
        return found

    async def update_document(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
        """Update specific fields in a document."""
        doc_ref = self.collection(collection).document(doc_id)
        await doc_ref.update(data)

    async def delete_document(self, collection: str, doc_id: str) -> None:
        """Delete a document."""
        doc_ref = self.collection(collection).document(doc_id)
        await doc_ref.delete()

    async def query_documents(
        self,
        collection: str,
        filters: list[tuple[str, str, Any]] | None = None,
        order_by: str | None = None,
        order_direction: str = "ASCENDING",
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        """Query documents with filters.

        Args:
            collection: Collection name
            filters: List of (field, operator, value) tuples
            order_by: Field to order by
            order_direction: ASCENDING or DESCENDING
            limit: Max documents to return
            offset: Number of documents to skip

        Returns:
            List of document dictionaries with IDs
        """
        query: firestore.AsyncCollectionReference | firestore.AsyncQuery = self.collection(collection)

        if filters:
            for field, op, value in filters:
                query = query.where(field, op, value)

        if order_by:
            direction = firestore.Query.DESCENDING if order_direction == "DESCENDING" else firestore.Query.ASCENDING
            query = query.order_by(order_by, direction=direction)

        if offset:
            query = query.offset(offset)

        if limit:
            query = query.limit(limit)

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
                query = query.where(field, op, value)

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
        return result

    async def run_transaction(self, fn: Callable[[TransactionView], Awaitable[_T]]) -> _T:
        """Run ``fn`` in a Firestore transaction spanning any number of documents.

        ``fn`` must do all its reads before its first write and be free of other side
        effects: Firestore retries the whole transaction (calling ``fn`` again) on contention.
        """

        @firestore.async_transactional
        async def in_transaction(transaction: firestore.AsyncTransaction) -> _T:
            return await fn(TransactionView(self, transaction))

        result: _T = await in_transaction(self.client.transaction())
        return result
