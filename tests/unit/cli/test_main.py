"""Tests for CLI main module."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from karaoke_decide.cli.main import cli, get_catalog_service


class TestCli:
    """Tests for main CLI group."""

    def test_cli_help(self) -> None:
        """Test CLI shows help."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert "Nomad Karaoke Decide" in result.output

    def test_cli_version(self) -> None:
        """Test CLI shows version."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])

        assert result.exit_code == 0
        assert "karaoke-decide" in result.output

    def test_cli_verbose_option(self) -> None:
        """Test CLI accepts verbose option."""
        runner = CliRunner()
        result = runner.invoke(cli, ["-v", "--help"])

        assert result.exit_code == 0


class TestAuthCommands:
    """Tests for auth command group."""

    def test_auth_login(self) -> None:
        """Test auth login stub."""
        runner = CliRunner()
        result = runner.invoke(cli, ["auth", "login"])

        assert result.exit_code == 0
        assert "not yet implemented" in result.output

    def test_auth_status(self) -> None:
        """Test auth status stub."""
        runner = CliRunner()
        result = runner.invoke(cli, ["auth", "status"])

        assert result.exit_code == 0
        assert "not yet implemented" in result.output

    def test_auth_logout(self) -> None:
        """Test auth logout stub."""
        runner = CliRunner()
        result = runner.invoke(cli, ["auth", "logout"])

        assert result.exit_code == 0
        assert "not yet implemented" in result.output


class TestServicesCommands:
    """Tests for services command group."""

    def test_services_list(self) -> None:
        """Test services list stub."""
        runner = CliRunner()
        result = runner.invoke(cli, ["services", "list"])

        assert result.exit_code == 0
        assert "not yet implemented" in result.output

    def test_services_connect_spotify(self) -> None:
        """Test services connect spotify stub."""
        runner = CliRunner()
        result = runner.invoke(cli, ["services", "connect", "spotify"])

        assert result.exit_code == 0
        assert "not yet implemented" in result.output

    def test_services_connect_lastfm(self) -> None:
        """Test services connect lastfm stub."""
        runner = CliRunner()
        result = runner.invoke(cli, ["services", "connect", "lastfm"])

        assert result.exit_code == 0
        assert "not yet implemented" in result.output

    def test_services_sync(self) -> None:
        """Test services sync stub."""
        runner = CliRunner()
        result = runner.invoke(cli, ["services", "sync"])

        assert result.exit_code == 0
        assert "not yet implemented" in result.output


class TestSongsCommands:
    """Tests for songs command group."""

    @patch("karaoke_decide.cli.main.get_catalog_service")
    def test_songs_search(self, mock_get_service: MagicMock) -> None:
        """Test songs search command."""
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.id = 1
        mock_result.artist = "Queen"
        mock_result.title = "Bohemian Rhapsody"
        mock_result.brand_count = 5
        mock_service.search_songs.return_value = [mock_result]
        mock_get_service.return_value = mock_service

        runner = CliRunner()
        result = runner.invoke(cli, ["songs", "search", "bohemian"])

        assert result.exit_code == 0
        assert "Queen" in result.output
        assert "Bohemian Rhapsody" in result.output

    @patch("karaoke_decide.cli.main.get_catalog_service")
    def test_songs_search_no_results(self, mock_get_service: MagicMock) -> None:
        """Test songs search with no results."""
        mock_service = MagicMock()
        mock_service.search_songs.return_value = []
        mock_get_service.return_value = mock_service

        runner = CliRunner()
        result = runner.invoke(cli, ["songs", "search", "nonexistent"])

        assert result.exit_code == 0
        assert "No results found" in result.output

    @patch("karaoke_decide.cli.main.get_catalog_service")
    def test_songs_artist(self, mock_get_service: MagicMock) -> None:
        """Test songs artist command."""
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.id = 1
        mock_result.title = "We Are The Champions"
        mock_result.brand_count = 3
        mock_service.get_songs_by_artist.return_value = [mock_result]
        mock_get_service.return_value = mock_service

        runner = CliRunner()
        result = runner.invoke(cli, ["songs", "artist", "Queen"])

        assert result.exit_code == 0
        assert "We Are The Champions" in result.output

    @patch("karaoke_decide.cli.main.get_catalog_service")
    def test_songs_artist_no_results(self, mock_get_service: MagicMock) -> None:
        """Test songs artist with no results."""
        mock_service = MagicMock()
        mock_service.get_songs_by_artist.return_value = []
        mock_get_service.return_value = mock_service

        runner = CliRunner()
        result = runner.invoke(cli, ["songs", "artist", "Unknown"])

        assert result.exit_code == 0
        assert "No songs found" in result.output

    @patch("karaoke_decide.cli.main.get_catalog_service")
    def test_songs_popular(self, mock_get_service: MagicMock) -> None:
        """Test songs popular command."""
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.id = 1
        mock_result.artist = "Journey"
        mock_result.title = "Don't Stop Believin'"
        mock_result.brand_count = 10
        mock_service.get_popular_songs.return_value = [mock_result]
        mock_get_service.return_value = mock_service

        runner = CliRunner()
        result = runner.invoke(cli, ["songs", "popular"])

        assert result.exit_code == 0
        assert "Journey" in result.output

    @patch("karaoke_decide.cli.main.get_catalog_service")
    def test_songs_popular_no_results(self, mock_get_service: MagicMock) -> None:
        """Test songs popular with no results."""
        mock_service = MagicMock()
        mock_service.get_popular_songs.return_value = []
        mock_get_service.return_value = mock_service

        runner = CliRunner()
        result = runner.invoke(cli, ["songs", "popular"])

        assert result.exit_code == 0
        assert "No popular songs found" in result.output

    @patch("karaoke_decide.cli.main.get_catalog_service")
    def test_songs_stats(self, mock_get_service: MagicMock) -> None:
        """Test songs stats command."""
        mock_service = MagicMock()
        mock_service.get_stats.return_value = {
            "total_songs": 275809,
            "unique_artists": 50000,
            "max_brand_count": 10,
            "avg_brand_count": 2.5,
        }
        mock_get_service.return_value = mock_service

        runner = CliRunner()
        result = runner.invoke(cli, ["songs", "stats"])

        assert result.exit_code == 0
        assert "275,809" in result.output
        assert "50,000" in result.output

    def test_songs_browse(self) -> None:
        """Test songs browse stub."""
        runner = CliRunner()
        result = runner.invoke(cli, ["songs", "browse"])

        assert result.exit_code == 0
        assert "not yet implemented" in result.output

    def test_songs_mine(self) -> None:
        """Test songs mine stub."""
        runner = CliRunner()
        result = runner.invoke(cli, ["songs", "mine"])

        assert result.exit_code == 0
        assert "not yet implemented" in result.output

    def test_songs_top(self) -> None:
        """Test songs top stub."""
        runner = CliRunner()
        result = runner.invoke(cli, ["songs", "top"])

        assert result.exit_code == 0
        assert "not yet implemented" in result.output


class TestPlaylistCommands:
    """Tests for playlist command group."""

    def test_playlist_list(self) -> None:
        """Test playlist list stub."""
        runner = CliRunner()
        result = runner.invoke(cli, ["playlist", "list"])

        assert result.exit_code == 0
        assert "not yet implemented" in result.output

    def test_playlist_create(self) -> None:
        """Test playlist create stub."""
        runner = CliRunner()
        result = runner.invoke(cli, ["playlist", "create", "My Playlist"])

        assert result.exit_code == 0
        assert "not yet implemented" in result.output

    def test_playlist_show(self) -> None:
        """Test playlist show stub."""
        runner = CliRunner()
        result = runner.invoke(cli, ["playlist", "show", "playlist123"])

        assert result.exit_code == 0
        assert "not yet implemented" in result.output

    def test_playlist_add(self) -> None:
        """Test playlist add stub."""
        runner = CliRunner()
        result = runner.invoke(cli, ["playlist", "add", "playlist123", "song456"])

        assert result.exit_code == 0
        assert "not yet implemented" in result.output

    def test_playlist_remove(self) -> None:
        """Test playlist remove stub."""
        runner = CliRunner()
        result = runner.invoke(cli, ["playlist", "remove", "playlist123", "song456"])

        assert result.exit_code == 0
        assert "not yet implemented" in result.output


class TestOtherCommands:
    """Tests for other CLI commands."""

    def test_sung_command(self) -> None:
        """Test sung command stub."""
        runner = CliRunner()
        result = runner.invoke(cli, ["sung", "song123"])

        assert result.exit_code == 0
        assert "not yet implemented" in result.output

    def test_history_command(self) -> None:
        """Test history command stub."""
        runner = CliRunner()
        result = runner.invoke(cli, ["history"])

        assert result.exit_code == 0
        assert "not yet implemented" in result.output


class TestGetCatalogService:
    """Tests for get_catalog_service function."""

    @patch("karaoke_decide.cli.main.BigQueryCatalogService")
    def test_creates_service_once(self, mock_service_class: MagicMock) -> None:
        """Test service is created lazily and cached."""
        # Reset the global state
        import karaoke_decide.cli.main as cli_module

        cli_module._catalog_service = None

        # First call creates service
        service1 = get_catalog_service()
        mock_service_class.assert_called_once()

        # Second call returns cached service
        service2 = get_catalog_service()
        assert service1 is service2
        mock_service_class.assert_called_once()  # Still only one call
