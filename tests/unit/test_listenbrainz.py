"""Tests for ListenBrainz service."""

from karaoke_decide.services.listenbrainz import (
    ListenBrainzClient,
    ListenBrainzUserInfo,
)


class TestListenBrainzDataclasses:
    """Tests for ListenBrainz dataclasses."""

    def test_user_info(self) -> None:
        """Test ListenBrainzUserInfo dataclass."""
        user_info = ListenBrainzUserInfo(
            username="test_user",
            listen_count=1000,
        )
        assert user_info.username == "test_user"
        assert user_info.listen_count == 1000


class TestListenBrainzClient:
    """Tests for ListenBrainzClient."""

    def test_client_constants(self) -> None:
        """Test ListenBrainzClient has expected constants."""
        assert hasattr(ListenBrainzClient, "API_BASE")
        assert "listenbrainz" in ListenBrainzClient.API_BASE.lower()
