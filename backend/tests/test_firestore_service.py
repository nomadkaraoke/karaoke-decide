"""Tests for Firestore service."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config import BackendSettings
from backend.services.firestore_service import FirestoreService


@pytest.fixture
def mock_settings() -> BackendSettings:
    """Create mock settings."""
    return BackendSettings(
        google_cloud_project="test-project",
        firestore_database="(default)",
    )


@pytest.fixture
def firestore_service(mock_settings: BackendSettings) -> FirestoreService:
    """Create FirestoreService with mock settings."""
    return FirestoreService(mock_settings)


class TestFirestoreServiceInit:
    """Tests for FirestoreService initialization."""

    def test_init_with_settings(self, mock_settings: BackendSettings) -> None:
        """Test service initialization with settings."""
        service = FirestoreService(mock_settings)
        assert service.settings == mock_settings
        assert service._client is None

    @patch("backend.services.firestore_service.firestore.AsyncClient")
    def test_client_property_creates_client(
        self, mock_async_client: MagicMock, firestore_service: FirestoreService
    ) -> None:
        """Test client property creates async client on first access."""
        _ = firestore_service.client
        mock_async_client.assert_called_once()

    @patch("backend.services.firestore_service.firestore.AsyncClient")
    def test_client_property_caches_client(
        self, mock_async_client: MagicMock, firestore_service: FirestoreService
    ) -> None:
        """Test client property caches the client."""
        client1 = firestore_service.client
        client2 = firestore_service.client
        assert client1 is client2
        mock_async_client.assert_called_once()


class TestFirestoreServiceCollection:
    """Tests for collection method."""

    @patch("backend.services.firestore_service.firestore.AsyncClient")
    def test_collection_returns_reference(
        self, mock_async_client: MagicMock, firestore_service: FirestoreService
    ) -> None:
        """Test collection method returns collection reference."""
        mock_client = mock_async_client.return_value
        mock_collection = MagicMock()
        mock_client.collection.return_value = mock_collection

        result = firestore_service.collection("users")

        mock_client.collection.assert_called_once_with("users")
        assert result == mock_collection


class TestFirestoreServiceGetDocument:
    """Tests for get_document method."""

    @pytest.mark.asyncio
    @patch("backend.services.firestore_service.firestore.AsyncClient")
    async def test_get_document_found(
        self, mock_async_client: MagicMock, firestore_service: FirestoreService
    ) -> None:
        """Test getting a document that exists."""
        mock_client = mock_async_client.return_value
        mock_doc_ref = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.id = "user123"
        mock_doc.to_dict.return_value = {"name": "Test User", "email": "test@example.com"}
        mock_doc_ref.get = AsyncMock(return_value=mock_doc)
        mock_client.collection.return_value.document.return_value = mock_doc_ref

        result = await firestore_service.get_document("users", "user123")

        assert result is not None
        assert result["id"] == "user123"
        assert result["name"] == "Test User"

    @pytest.mark.asyncio
    @patch("backend.services.firestore_service.firestore.AsyncClient")
    async def test_get_document_not_found(
        self, mock_async_client: MagicMock, firestore_service: FirestoreService
    ) -> None:
        """Test getting a document that doesn't exist."""
        mock_client = mock_async_client.return_value
        mock_doc_ref = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = False
        mock_doc_ref.get = AsyncMock(return_value=mock_doc)
        mock_client.collection.return_value.document.return_value = mock_doc_ref

        result = await firestore_service.get_document("users", "nonexistent")

        assert result is None


class TestFirestoreServiceSetDocument:
    """Tests for set_document method."""

    @pytest.mark.asyncio
    @patch("backend.services.firestore_service.firestore.AsyncClient")
    async def test_set_document(
        self, mock_async_client: MagicMock, firestore_service: FirestoreService
    ) -> None:
        """Test setting a document."""
        mock_client = mock_async_client.return_value
        mock_doc_ref = MagicMock()
        mock_doc_ref.set = AsyncMock()
        mock_client.collection.return_value.document.return_value = mock_doc_ref

        await firestore_service.set_document(
            "users", "user123", {"name": "Test User"}
        )

        mock_doc_ref.set.assert_called_once_with({"name": "Test User"}, merge=False)

    @pytest.mark.asyncio
    @patch("backend.services.firestore_service.firestore.AsyncClient")
    async def test_set_document_with_merge(
        self, mock_async_client: MagicMock, firestore_service: FirestoreService
    ) -> None:
        """Test setting a document with merge."""
        mock_client = mock_async_client.return_value
        mock_doc_ref = MagicMock()
        mock_doc_ref.set = AsyncMock()
        mock_client.collection.return_value.document.return_value = mock_doc_ref

        await firestore_service.set_document(
            "users", "user123", {"name": "Updated"}, merge=True
        )

        mock_doc_ref.set.assert_called_once_with({"name": "Updated"}, merge=True)


class TestFirestoreServiceUpdateDocument:
    """Tests for update_document method."""

    @pytest.mark.asyncio
    @patch("backend.services.firestore_service.firestore.AsyncClient")
    async def test_update_document(
        self, mock_async_client: MagicMock, firestore_service: FirestoreService
    ) -> None:
        """Test updating a document."""
        mock_client = mock_async_client.return_value
        mock_doc_ref = MagicMock()
        mock_doc_ref.update = AsyncMock()
        mock_client.collection.return_value.document.return_value = mock_doc_ref

        await firestore_service.update_document(
            "users", "user123", {"last_login": "2024-01-01"}
        )

        mock_doc_ref.update.assert_called_once_with({"last_login": "2024-01-01"})


class TestFirestoreServiceDeleteDocument:
    """Tests for delete_document method."""

    @pytest.mark.asyncio
    @patch("backend.services.firestore_service.firestore.AsyncClient")
    async def test_delete_document(
        self, mock_async_client: MagicMock, firestore_service: FirestoreService
    ) -> None:
        """Test deleting a document."""
        mock_client = mock_async_client.return_value
        mock_doc_ref = MagicMock()
        mock_doc_ref.delete = AsyncMock()
        mock_client.collection.return_value.document.return_value = mock_doc_ref

        await firestore_service.delete_document("users", "user123")

        mock_doc_ref.delete.assert_called_once()


class TestFirestoreServiceQueryDocuments:
    """Tests for query_documents method."""

    @pytest.mark.asyncio
    @patch("backend.services.firestore_service.firestore.AsyncClient")
    async def test_query_documents_basic(
        self, mock_async_client: MagicMock, firestore_service: FirestoreService
    ) -> None:
        """Test querying documents without filters."""
        mock_client = mock_async_client.return_value
        mock_query = MagicMock()

        # Create async generator for stream
        async def async_stream():
            doc = MagicMock()
            doc.id = "user1"
            doc.to_dict.return_value = {"name": "User 1"}
            yield doc

        mock_query.stream = async_stream
        mock_client.collection.return_value = mock_query

        result = await firestore_service.query_documents("users")

        assert len(result) == 1
        assert result[0]["id"] == "user1"

    @pytest.mark.asyncio
    @patch("backend.services.firestore_service.firestore.AsyncClient")
    async def test_query_documents_with_filters(
        self, mock_async_client: MagicMock, firestore_service: FirestoreService
    ) -> None:
        """Test querying documents with filters."""
        mock_client = mock_async_client.return_value
        mock_query = MagicMock()

        async def async_stream():
            return
            yield  # Make it an async generator that yields nothing

        mock_query.where.return_value = mock_query
        mock_query.stream = async_stream
        mock_client.collection.return_value = mock_query

        await firestore_service.query_documents(
            "users",
            filters=[("status", "==", "active")],
        )

        mock_query.where.assert_called_once_with("status", "==", "active")

    @pytest.mark.asyncio
    @patch("backend.services.firestore_service.firestore.AsyncClient")
    async def test_query_documents_with_order(
        self, mock_async_client: MagicMock, firestore_service: FirestoreService
    ) -> None:
        """Test querying documents with ordering."""
        mock_client = mock_async_client.return_value
        mock_query = MagicMock()

        async def async_stream():
            return
            yield

        mock_query.order_by.return_value = mock_query
        mock_query.stream = async_stream
        mock_client.collection.return_value = mock_query

        await firestore_service.query_documents(
            "users",
            order_by="created_at",
            order_direction="DESCENDING",
        )

        mock_query.order_by.assert_called_once()

    @pytest.mark.asyncio
    @patch("backend.services.firestore_service.firestore.AsyncClient")
    async def test_query_documents_with_limit(
        self, mock_async_client: MagicMock, firestore_service: FirestoreService
    ) -> None:
        """Test querying documents with limit."""
        mock_client = mock_async_client.return_value
        mock_query = MagicMock()

        async def async_stream():
            return
            yield

        mock_query.limit.return_value = mock_query
        mock_query.stream = async_stream
        mock_client.collection.return_value = mock_query

        await firestore_service.query_documents("users", limit=10)

        mock_query.limit.assert_called_once_with(10)


class TestFirestoreServiceCountDocuments:
    """Tests for count_documents method."""

    @pytest.mark.asyncio
    @patch("backend.services.firestore_service.firestore.AsyncClient")
    async def test_count_documents(
        self, mock_async_client: MagicMock, firestore_service: FirestoreService
    ) -> None:
        """Test counting documents."""
        mock_client = mock_async_client.return_value
        mock_query = MagicMock()
        mock_count_query = MagicMock()
        mock_count_result = MagicMock()
        mock_count_result.value = 42
        mock_count_query.get = AsyncMock(return_value=[[mock_count_result]])
        mock_query.count.return_value = mock_count_query
        mock_client.collection.return_value = mock_query

        result = await firestore_service.count_documents("users")

        assert result == 42
