"""Firestore database service."""

from typing import Any

from google.cloud import firestore

from backend.config import BackendSettings


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

    async def get_document(
        self, collection: str, doc_id: str
    ) -> dict[str, Any] | None:
        """Get a document by ID."""
        doc_ref = self.collection(collection).document(doc_id)
        doc = await doc_ref.get()
        if doc.exists:
            return {"id": doc.id, **doc.to_dict()}
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

    async def update_document(
        self, collection: str, doc_id: str, data: dict[str, Any]
    ) -> None:
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
        query = self.collection(collection)

        if filters:
            for field, op, value in filters:
                query = query.where(field, op, value)

        if order_by:
            direction = (
                firestore.Query.DESCENDING
                if order_direction == "DESCENDING"
                else firestore.Query.ASCENDING
            )
            query = query.order_by(order_by, direction=direction)

        if offset:
            query = query.offset(offset)

        if limit:
            query = query.limit(limit)

        docs = []
        async for doc in query.stream():
            docs.append({"id": doc.id, **doc.to_dict()})

        return docs

    async def count_documents(
        self,
        collection: str,
        filters: list[tuple[str, str, Any]] | None = None,
    ) -> int:
        """Count documents matching filters."""
        query = self.collection(collection)

        if filters:
            for field, op, value in filters:
                query = query.where(field, op, value)

        # Use count aggregation
        count_query = query.count()
        result = await count_query.get()
        return result[0][0].value
