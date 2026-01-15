"""Tests for admin routes."""

from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.services.auth_service import AuthService
from karaoke_decide.core.models import User


@pytest.fixture
def sample_admin_user() -> User:
    """Create a sample admin user for testing."""
    return User(
        id="admin_user_123",
        email="admin@example.com",
        display_name="Admin User",
        is_admin=True,
        is_guest=False,
        created_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        updated_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        total_songs_known=50,
        total_songs_sung=10,
        last_sync_at=datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC),
    )


@pytest.fixture
def sample_non_admin_user() -> User:
    """Create a sample non-admin user for testing."""
    return User(
        id="regular_user_456",
        email="user@example.com",
        display_name="Regular User",
        is_admin=False,
        is_guest=False,
        created_at=datetime(2024, 1, 5, 12, 0, 0, tzinfo=UTC),
        updated_at=datetime(2024, 1, 5, 12, 0, 0, tzinfo=UTC),
        total_songs_known=20,
        total_songs_sung=5,
        last_sync_at=None,
    )


@pytest.fixture
def sample_target_user() -> User:
    """Create a sample target user for impersonation testing."""
    return User(
        id="target_user_789",
        email="target@example.com",
        display_name="Target User",
        is_admin=False,
        is_guest=False,
        created_at=datetime(2024, 1, 10, 12, 0, 0, tzinfo=UTC),
        updated_at=datetime(2024, 1, 10, 12, 0, 0, tzinfo=UTC),
        total_songs_known=30,
        total_songs_sung=8,
        last_sync_at=None,
    )


@pytest.fixture
def mock_auth_service(sample_target_user: User) -> MagicMock:
    """Create a mock auth service for impersonation testing."""
    mock = MagicMock(spec=AuthService)
    mock.get_user_by_id = AsyncMock(return_value=sample_target_user)
    mock.generate_jwt = MagicMock(return_value=("test-token-123", 604800))
    mock.generate_guest_jwt = MagicMock(return_value=("guest-token-123", 2592000))
    return mock


@pytest.fixture
def mock_admin_firestore_service() -> MagicMock:
    """Create a mock Firestore service for admin testing."""
    mock = MagicMock()
    mock.count_documents = AsyncMock(return_value=10)
    mock.query_documents = AsyncMock(return_value=[])
    mock.get_document = AsyncMock(return_value=None)
    return mock


@pytest.fixture
def admin_client(
    mock_admin_firestore_service: MagicMock,
    sample_admin_user: User,
    mock_catalog_service: MagicMock,
) -> Generator[TestClient, None, None]:
    """Create test client with admin user."""
    with patch(
        "backend.api.routes.catalog.get_catalog_service",
        return_value=mock_catalog_service,
    ):
        from backend.api.deps import get_current_user, get_firestore
        from backend.main import app

        async def override_get_current_user() -> User:
            return sample_admin_user

        async def override_get_firestore() -> MagicMock:
            return mock_admin_firestore_service

        app.dependency_overrides[get_current_user] = override_get_current_user
        app.dependency_overrides[get_firestore] = override_get_firestore

        yield TestClient(app)

        app.dependency_overrides.clear()


@pytest.fixture
def non_admin_client(
    mock_admin_firestore_service: MagicMock,
    sample_non_admin_user: User,
    mock_catalog_service: MagicMock,
) -> Generator[TestClient, None, None]:
    """Create test client with non-admin user."""
    with patch(
        "backend.api.routes.catalog.get_catalog_service",
        return_value=mock_catalog_service,
    ):
        from backend.api.deps import get_current_user, get_firestore
        from backend.main import app

        async def override_get_current_user() -> User:
            return sample_non_admin_user

        async def override_get_firestore() -> MagicMock:
            return mock_admin_firestore_service

        app.dependency_overrides[get_current_user] = override_get_current_user
        app.dependency_overrides[get_firestore] = override_get_firestore

        yield TestClient(app)

        app.dependency_overrides.clear()


class TestAdminAuthorization:
    """Test admin authorization requirements."""

    def test_stats_requires_admin(self, non_admin_client: TestClient) -> None:
        """Non-admin users should get 403 for stats endpoint."""
        response = non_admin_client.get(
            "/api/admin/stats",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 403
        assert "Admin access required" in response.json()["detail"]

    def test_users_list_requires_admin(self, non_admin_client: TestClient) -> None:
        """Non-admin users should get 403 for users list endpoint."""
        response = non_admin_client.get(
            "/api/admin/users",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 403

    def test_user_detail_requires_admin(self, non_admin_client: TestClient) -> None:
        """Non-admin users should get 403 for user detail endpoint."""
        response = non_admin_client.get(
            "/api/admin/users/some-user-id",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 403

    def test_sync_jobs_list_requires_admin(self, non_admin_client: TestClient) -> None:
        """Non-admin users should get 403 for sync jobs list endpoint."""
        response = non_admin_client.get(
            "/api/admin/sync-jobs",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 403

    def test_sync_job_detail_requires_admin(self, non_admin_client: TestClient) -> None:
        """Non-admin users should get 403 for sync job detail endpoint."""
        response = non_admin_client.get(
            "/api/admin/sync-jobs/some-job-id",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 403


class TestAdminStats:
    """Test GET /api/admin/stats endpoint."""

    def test_get_stats_success(
        self,
        admin_client: TestClient,
        mock_admin_firestore_service: MagicMock,
    ) -> None:
        """Admin should be able to get stats."""

        # Configure mock to return different counts for different queries
        async def mock_count(collection: str, filters: list | None = None) -> int:
            if collection == "decide_users":
                filter_dict = {f[0]: f[2] for f in filters} if filters else {}
                # Stats queries use decide_users collection (karaoke-decide only)
                # Verified users are counted by email existence (not is_guest field)
                if "email" in filter_dict:  # email != None filter for verified
                    return 60
                if filter_dict.get("is_guest") is True:
                    return 40
                if "last_sync_at" in filter_dict:
                    return 25
                # Total users query (no filter)
                return 100
            if collection == "sync_jobs":
                filter_dict = {f[0]: f[2] for f in filters} if filters else {}
                status = filter_dict.get("status")
                if status == "pending":
                    return 5
                if status == "in_progress":
                    return 3
                if status == "completed":
                    return 50
                if status == "failed":
                    return 2
                return 60
            if collection == "music_services":
                filter_dict = {f[0]: f[2] for f in filters} if filters else {}
                if filter_dict.get("service_type") == "spotify":
                    return 45
                if filter_dict.get("service_type") == "lastfm":
                    return 20
            return 0

        mock_admin_firestore_service.count_documents = AsyncMock(side_effect=mock_count)

        response = admin_client.get(
            "/api/admin/stats",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

        data = response.json()
        assert "users" in data
        assert "sync_jobs" in data
        assert "services" in data

        # Check user stats
        assert data["users"]["total"] == 100
        assert data["users"]["verified"] == 60
        assert data["users"]["guests"] == 40
        assert data["users"]["active_7d"] == 25

        # Check sync job stats
        assert data["sync_jobs"]["pending"] == 5
        assert data["sync_jobs"]["in_progress"] == 3
        assert data["sync_jobs"]["completed"] == 50
        assert data["sync_jobs"]["failed"] == 2

        # Check service stats
        assert data["services"]["spotify_connected"] == 45
        assert data["services"]["lastfm_connected"] == 20


class TestAdminUsersList:
    """Test GET /api/admin/users endpoint."""

    def test_list_users_success(
        self,
        admin_client: TestClient,
        mock_admin_firestore_service: MagicMock,
    ) -> None:
        """Admin should be able to list users (default filter is verified)."""
        mock_admin_firestore_service.count_documents = AsyncMock(return_value=50)
        mock_admin_firestore_service.query_documents = AsyncMock(
            return_value=[
                {
                    "user_id": "user1",
                    "email": "user1@example.com",
                    "display_name": "User One",
                    "is_guest": False,
                    "is_admin": False,
                    "created_at": datetime(2024, 1, 1, tzinfo=UTC),
                    "last_sync_at": datetime(2024, 1, 10, tzinfo=UTC),
                    "quiz_completed_at": None,
                    "total_songs_known": 10,
                },
                {
                    "user_id": "user2",
                    "email": "user2@example.com",
                    "display_name": "User Two",
                    "is_guest": False,
                    "is_admin": False,
                    "created_at": datetime(2024, 1, 2, tzinfo=UTC),
                    "last_sync_at": None,
                    "quiz_completed_at": None,
                    "total_songs_known": 5,
                },
                {
                    "user_id": "guest1",
                    "email": None,  # Guest has no email - filtered out by default
                    "display_name": None,
                    "is_guest": True,
                    "is_admin": False,
                    "created_at": datetime(2024, 1, 3, tzinfo=UTC),
                    "last_sync_at": None,
                    "quiz_completed_at": None,
                    "total_songs_known": 0,
                },
            ]
        )

        # Default filter is "verified" - guests are filtered out client-side
        response = admin_client.get(
            "/api/admin/users",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

        data = response.json()
        # Total is calculated client-side for verified filter (only users with email)
        assert data["total"] == 2
        assert len(data["users"]) == 2
        assert data["users"][0]["id"] == "user1"
        assert data["users"][0]["email"] == "user1@example.com"
        # Guest user is filtered out (no email)

    def test_list_users_with_filter(
        self,
        admin_client: TestClient,
        mock_admin_firestore_service: MagicMock,
    ) -> None:
        """Admin should be able to filter users."""
        mock_admin_firestore_service.count_documents = AsyncMock(return_value=20)
        mock_admin_firestore_service.query_documents = AsyncMock(return_value=[])

        response = admin_client.get(
            "/api/admin/users?filter=verified",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

        # Verified filter is now client-side (no Firestore filter)
        # to avoid requiring composite index
        call_args = mock_admin_firestore_service.query_documents.call_args
        assert call_args[1].get("filters") is None  # No server-side filter for verified

    def test_list_users_with_pagination(
        self,
        admin_client: TestClient,
        mock_admin_firestore_service: MagicMock,
    ) -> None:
        """Admin should be able to paginate users (with filter=all for server-side pagination)."""
        mock_admin_firestore_service.count_documents = AsyncMock(return_value=100)
        mock_admin_firestore_service.query_documents = AsyncMock(return_value=[])

        # Use filter=all to test server-side pagination
        # (verified filter uses client-side pagination)
        response = admin_client.get(
            "/api/admin/users?filter=all&limit=10&offset=20",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

        # Verify pagination was applied server-side
        call_args = mock_admin_firestore_service.query_documents.call_args
        assert call_args[1].get("limit") == 10
        assert call_args[1].get("offset") == 20

    def test_list_users_with_search(
        self,
        admin_client: TestClient,
        mock_admin_firestore_service: MagicMock,
    ) -> None:
        """Admin should be able to search users by email."""
        mock_admin_firestore_service.count_documents = AsyncMock(return_value=100)
        mock_admin_firestore_service.query_documents = AsyncMock(
            return_value=[
                {
                    "user_id": "user1",
                    "email": "test@example.com",
                    "display_name": "Test User",
                    "is_guest": False,
                    "is_admin": False,
                    "created_at": datetime(2024, 1, 1, tzinfo=UTC),
                    "last_sync_at": None,
                    "quiz_completed_at": None,
                    "total_songs_known": 0,
                },
                {
                    "user_id": "user2",
                    "email": "other@example.com",
                    "display_name": "Other User",
                    "is_guest": False,
                    "is_admin": False,
                    "created_at": datetime(2024, 1, 2, tzinfo=UTC),
                    "last_sync_at": None,
                    "quiz_completed_at": None,
                    "total_songs_known": 0,
                },
            ]
        )

        response = admin_client.get(
            "/api/admin/users?search=test",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

        data = response.json()
        # Search should filter client-side
        assert data["total"] == 1
        assert len(data["users"]) == 1
        assert "test" in data["users"][0]["email"].lower()


class TestAdminUserDetail:
    """Test GET /admin/users/{user_id} endpoint."""

    def test_get_user_detail_success(
        self,
        admin_client: TestClient,
        mock_admin_firestore_service: MagicMock,
    ) -> None:
        """Admin should be able to get user detail."""
        user_doc = {
            "user_id": "user_123",
            "email": "user@example.com",
            "display_name": "Test User",
            "is_guest": False,
            "is_admin": False,
            "created_at": datetime(2024, 1, 1, tzinfo=UTC),
            "last_sync_at": datetime(2024, 1, 10, tzinfo=UTC),
            "quiz_completed_at": datetime(2024, 1, 5, tzinfo=UTC),
            "total_songs_known": 50,
        }
        service_docs = [
            {
                "service_type": "spotify",
                "service_username": "testuser",
                "sync_status": "idle",
                "last_sync_at": datetime(2024, 1, 10, tzinfo=UTC),
                "tracks_synced": 100,
                "sync_error": None,
            },
        ]
        sync_job_docs: list[dict[str, Any]] = []

        # For non-guest users, query_documents is called:
        # 1. First to find user by user_id
        # 2. Then for services
        # 3. Then for sync_jobs
        call_count = 0

        async def query_side_effect(
            collection: str, filters: list[Any] | None = None, **kwargs: Any
        ) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # User lookup
                return [user_doc]
            elif collection == "music_services":
                return service_docs
            elif collection == "sync_jobs":
                return sync_job_docs
            return []

        mock_admin_firestore_service.query_documents = AsyncMock(side_effect=query_side_effect)
        mock_admin_firestore_service.count_documents = AsyncMock(return_value=10)

        response = admin_client.get(
            "/api/admin/users/user_123",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == "user_123"
        assert data["email"] == "user@example.com"
        assert len(data["services"]) == 1
        assert data["services"][0]["service_type"] == "spotify"
        assert data["data_summary"]["artists_count"] == 10

    def test_get_user_detail_not_found(
        self,
        admin_client: TestClient,
        mock_admin_firestore_service: MagicMock,
    ) -> None:
        """Should return 404 for non-existent user."""
        # For non-guest users, query_documents returns empty list
        mock_admin_firestore_service.query_documents = AsyncMock(return_value=[])

        response = admin_client.get(
            "/api/admin/users/user_nonexistent",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 404
        assert "User not found" in response.json()["detail"]


class TestAdminSyncJobsList:
    """Test GET /admin/sync-jobs endpoint."""

    def test_list_sync_jobs_success(
        self,
        admin_client: TestClient,
        mock_admin_firestore_service: MagicMock,
    ) -> None:
        """Admin should be able to list sync jobs."""
        mock_admin_firestore_service.count_documents = AsyncMock(return_value=30)

        # Mock data for both sync_jobs and users queries
        job_docs = [
            {
                "id": "job1",
                "user_id": "user1",
                "status": "completed",
                "created_at": datetime(2024, 1, 10, tzinfo=UTC),
                "completed_at": datetime(2024, 1, 10, 0, 5, tzinfo=UTC),
                "error": None,
            },
            {
                "id": "job2",
                "user_id": "user2",
                "status": "failed",
                "created_at": datetime(2024, 1, 11, tzinfo=UTC),
                "completed_at": datetime(2024, 1, 11, 0, 2, tzinfo=UTC),
                "error": "Connection timeout",
            },
        ]
        user_docs = [
            {"user_id": "user1", "email": "user1@example.com"},
            {"user_id": "user2", "email": "user2@example.com"},
        ]

        def query_side_effect(collection: str, filters: list[Any] | None = None, **kwargs: Any) -> list[dict[str, Any]]:
            if collection == "sync_jobs":
                return job_docs
            if collection == "decide_users" and filters:
                # Handle batch "in" query for user emails
                for field, op, value in filters:
                    if field == "user_id" and op == "in":
                        return [d for d in user_docs if d["user_id"] in value]
            return []

        mock_admin_firestore_service.query_documents = AsyncMock(side_effect=query_side_effect)

        response = admin_client.get(
            "/api/admin/sync-jobs",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 30
        assert len(data["jobs"]) == 2
        assert data["jobs"][0]["id"] == "job1"
        assert data["jobs"][0]["status"] == "completed"
        assert data["jobs"][0]["user_email"] == "user1@example.com"
        assert data["jobs"][1]["error"] == "Connection timeout"

    def test_list_sync_jobs_with_status_filter(
        self,
        admin_client: TestClient,
        mock_admin_firestore_service: MagicMock,
    ) -> None:
        """Admin should be able to filter sync jobs by status."""
        mock_admin_firestore_service.count_documents = AsyncMock(return_value=5)
        mock_admin_firestore_service.query_documents = AsyncMock(return_value=[])

        response = admin_client.get(
            "/api/admin/sync-jobs?status=failed",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

        # Verify filter was applied
        call_args = mock_admin_firestore_service.count_documents.call_args
        assert ("status", "==", "failed") in call_args[1].get("filters", [])


class TestAdminSyncJobDetail:
    """Test GET /admin/sync-jobs/{job_id} endpoint."""

    def test_get_sync_job_detail_success(
        self,
        admin_client: TestClient,
        mock_admin_firestore_service: MagicMock,
    ) -> None:
        """Admin should be able to get sync job detail."""
        mock_admin_firestore_service.get_document = AsyncMock(
            side_effect=lambda collection, doc_id: {
                ("sync_jobs", "job123"): {
                    "id": "job123",
                    "user_id": "user1",
                    "status": "completed",
                    "created_at": datetime(2024, 1, 10, tzinfo=UTC),
                    "completed_at": datetime(2024, 1, 10, 0, 5, tzinfo=UTC),
                    "error": None,
                    "progress": {
                        "current_service": "spotify",
                        "current_phase": "matching",
                        "total_tracks": 100,
                        "processed_tracks": 100,
                        "matched_tracks": 85,
                        "percentage": 100,
                    },
                    "results": [
                        {
                            "service_type": "spotify",
                            "tracks_fetched": 100,
                            "tracks_matched": 85,
                            "user_songs_created": 60,
                            "user_songs_updated": 25,
                            "artists_stored": 40,
                            "error": None,
                        },
                    ],
                },
                ("decide_users", "user1"): {
                    "email": "user1@example.com",
                },
            }.get((collection, doc_id))
        )

        response = admin_client.get(
            "/api/admin/sync-jobs/job123",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == "job123"
        assert data["status"] == "completed"
        assert data["user_email"] == "user1@example.com"
        assert data["progress"]["matched_tracks"] == 85
        assert len(data["results"]) == 1
        assert data["results"][0]["tracks_fetched"] == 100

    def test_get_sync_job_detail_not_found(
        self,
        admin_client: TestClient,
        mock_admin_firestore_service: MagicMock,
    ) -> None:
        """Should return 404 for non-existent sync job."""
        mock_admin_firestore_service.get_document = AsyncMock(return_value=None)

        response = admin_client.get(
            "/api/admin/sync-jobs/nonexistent",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 404
        assert "Sync job not found" in response.json()["detail"]

    def test_get_sync_job_with_error(
        self,
        admin_client: TestClient,
        mock_admin_firestore_service: MagicMock,
    ) -> None:
        """Should return job with error details."""
        mock_admin_firestore_service.get_document = AsyncMock(
            side_effect=lambda collection, doc_id: {
                ("sync_jobs", "job456"): {
                    "id": "job456",
                    "user_id": "user1",
                    "status": "failed",
                    "created_at": datetime(2024, 1, 10, tzinfo=UTC),
                    "completed_at": datetime(2024, 1, 10, 0, 2, tzinfo=UTC),
                    "error": "Spotify API rate limit exceeded",
                    "progress": None,
                    "results": [],
                },
                ("decide_users", "user1"): {
                    "email": "user1@example.com",
                },
            }.get((collection, doc_id))
        )

        response = admin_client.get(
            "/api/admin/sync-jobs/job456",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "failed"
        assert data["error"] == "Spotify API rate limit exceeded"


class TestAdminImpersonate:
    """Test POST /api/admin/impersonate endpoint."""

    @pytest.fixture
    def admin_client_with_auth(
        self,
        mock_admin_firestore_service: MagicMock,
        mock_auth_service: MagicMock,
        sample_admin_user: User,
        mock_catalog_service: MagicMock,
    ) -> Generator[TestClient, None, None]:
        """Create test client with admin user and auth service mock."""
        with patch(
            "backend.api.routes.catalog.get_catalog_service",
            return_value=mock_catalog_service,
        ):
            from backend.api.deps import get_auth_service_dep, get_current_user, get_firestore
            from backend.main import app

            async def override_get_current_user() -> User:
                return sample_admin_user

            async def override_get_firestore() -> MagicMock:
                return mock_admin_firestore_service

            async def override_get_auth_service() -> MagicMock:
                return mock_auth_service

            app.dependency_overrides[get_current_user] = override_get_current_user
            app.dependency_overrides[get_firestore] = override_get_firestore
            app.dependency_overrides[get_auth_service_dep] = override_get_auth_service

            yield TestClient(app)

            app.dependency_overrides.clear()

    def test_impersonate_requires_admin(self, non_admin_client: TestClient) -> None:
        """Non-admin users should get 403 for impersonate endpoint."""
        response = non_admin_client.post(
            "/api/admin/impersonate",
            json={"email": "target@example.com"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 403
        assert "Admin access required" in response.json()["detail"]

    def test_impersonate_by_user_id(
        self,
        admin_client_with_auth: TestClient,
        mock_auth_service: MagicMock,
        sample_target_user: User,
    ) -> None:
        """Admin should be able to impersonate by user_id."""
        response = admin_client_with_auth.post(
            "/api/admin/impersonate",
            json={"user_id": "target_user_789"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["token"] == "test-token-123"
        assert data["expires_in"] == 604800
        assert data["user_id"] == "target_user_789"
        assert data["user_email"] == "target@example.com"
        assert data["user_display_name"] == "Target User"

        # Verify auth service was called correctly
        mock_auth_service.get_user_by_id.assert_called_once_with("target_user_789")
        mock_auth_service.generate_jwt.assert_called_once_with(sample_target_user)

    def test_impersonate_by_email(
        self,
        admin_client_with_auth: TestClient,
        mock_admin_firestore_service: MagicMock,
        mock_auth_service: MagicMock,
        sample_target_user: User,
    ) -> None:
        """Admin should be able to impersonate by email."""
        # Mock email lookup
        mock_admin_firestore_service.query_documents = AsyncMock(
            return_value=[{"user_id": "target_user_789", "email": "target@example.com"}]
        )

        response = admin_client_with_auth.post(
            "/api/admin/impersonate",
            json={"email": "target@example.com"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["token"] == "test-token-123"
        assert data["user_id"] == "target_user_789"
        assert data["user_email"] == "target@example.com"

    def test_impersonate_guest_user(
        self,
        admin_client_with_auth: TestClient,
        mock_auth_service: MagicMock,
    ) -> None:
        """Impersonating a guest user should use guest JWT."""
        guest_user = User(
            id="guest_abc123",
            email=None,
            display_name=None,
            is_admin=False,
            is_guest=True,
            created_at=datetime(2024, 1, 15, tzinfo=UTC),
            updated_at=datetime(2024, 1, 15, tzinfo=UTC),
        )
        mock_auth_service.get_user_by_id = AsyncMock(return_value=guest_user)

        response = admin_client_with_auth.post(
            "/api/admin/impersonate",
            json={"user_id": "guest_abc123"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["token"] == "guest-token-123"
        assert data["expires_in"] == 2592000  # 30 days
        assert data["user_id"] == "guest_abc123"
        assert data["user_email"] is None

        # Verify guest JWT was generated
        mock_auth_service.generate_guest_jwt.assert_called_once_with(guest_user)

    def test_impersonate_user_not_found(
        self,
        admin_client_with_auth: TestClient,
        mock_auth_service: MagicMock,
    ) -> None:
        """Should return 404 if user is not found."""
        mock_auth_service.get_user_by_id = AsyncMock(return_value=None)

        response = admin_client_with_auth.post(
            "/api/admin/impersonate",
            json={"user_id": "nonexistent_user"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 404
        assert "User not found" in response.json()["detail"]

    def test_impersonate_email_not_found(
        self,
        admin_client_with_auth: TestClient,
        mock_admin_firestore_service: MagicMock,
    ) -> None:
        """Should return 404 if email is not found."""
        mock_admin_firestore_service.query_documents = AsyncMock(return_value=[])

        response = admin_client_with_auth.post(
            "/api/admin/impersonate",
            json={"email": "nonexistent@example.com"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 404
        assert "User not found" in response.json()["detail"]

    def test_impersonate_requires_user_id_or_email(
        self,
        admin_client_with_auth: TestClient,
    ) -> None:
        """Should return 400 if neither user_id nor email is provided."""
        response = admin_client_with_auth.post(
            "/api/admin/impersonate",
            json={},
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 400
        assert "Must provide either user_id or email" in response.json()["detail"]
