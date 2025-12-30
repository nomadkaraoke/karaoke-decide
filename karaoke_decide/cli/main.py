"""Main CLI entry point for Karaoke Decide."""

import click
from rich.console import Console

from karaoke_decide import __version__

console = Console()


@click.group()
@click.version_option(version=__version__, prog_name="karaoke-decide")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Nomad Karaoke Decide - Find your perfect karaoke songs."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


@cli.group()
def auth() -> None:
    """Authentication commands."""
    pass


@auth.command()
def login() -> None:
    """Log in with email (magic link)."""
    console.print("[yellow]Auth login not yet implemented[/yellow]")
    # TODO: Implement magic link flow


@auth.command()
def status() -> None:
    """Show current authentication status."""
    console.print("[yellow]Auth status not yet implemented[/yellow]")
    # TODO: Show current user


@auth.command()
def logout() -> None:
    """Log out and clear credentials."""
    console.print("[yellow]Auth logout not yet implemented[/yellow]")
    # TODO: Clear stored credentials


@cli.group()
def services() -> None:
    """Music service connection commands."""
    pass


@services.command(name="list")
def list_services() -> None:
    """List connected music services."""
    console.print("[yellow]Services list not yet implemented[/yellow]")
    # TODO: Show connected services


@services.command()
@click.argument("service", type=click.Choice(["spotify", "lastfm"]))
def connect(service: str) -> None:
    """Connect a music service (spotify, lastfm)."""
    console.print(f"[yellow]Connect {service} not yet implemented[/yellow]")
    # TODO: OAuth flow


@services.command()
def sync() -> None:
    """Sync listening history from connected services."""
    console.print("[yellow]Sync not yet implemented[/yellow]")
    # TODO: Trigger sync


@cli.group()
def songs() -> None:
    """Song discovery commands."""
    pass


@songs.command()
@click.argument("query")
def search(query: str) -> None:
    """Search the karaoke catalog."""
    console.print(f"[yellow]Search for '{query}' not yet implemented[/yellow]")
    # TODO: Search catalog


@songs.command()
def browse() -> None:
    """Interactive browse with filters."""
    console.print("[yellow]Browse not yet implemented[/yellow]")
    # TODO: Interactive browser


@songs.command()
def mine() -> None:
    """Show songs from my listening history."""
    console.print("[yellow]My songs not yet implemented[/yellow]")
    # TODO: Show matched songs


@songs.command()
def top() -> None:
    """Show my top karaoke recommendations."""
    console.print("[yellow]Top songs not yet implemented[/yellow]")
    # TODO: Show recommendations


@cli.group()
def playlist() -> None:
    """Playlist management commands."""
    pass


@playlist.command(name="list")
def list_playlists() -> None:
    """List my playlists."""
    console.print("[yellow]Playlist list not yet implemented[/yellow]")
    # TODO: List playlists


@playlist.command()
@click.argument("name")
def create(name: str) -> None:
    """Create a new playlist."""
    console.print(f"[yellow]Create playlist '{name}' not yet implemented[/yellow]")
    # TODO: Create playlist


@playlist.command()
@click.argument("playlist_id")
def show(playlist_id: str) -> None:
    """Show playlist details."""
    console.print(f"[yellow]Show playlist '{playlist_id}' not yet implemented[/yellow]")
    # TODO: Show playlist


@playlist.command()
@click.argument("playlist_id")
@click.argument("song_id")
def add(playlist_id: str, song_id: str) -> None:
    """Add a song to a playlist."""
    console.print(f"[yellow]Add song to playlist not yet implemented[/yellow]")
    # TODO: Add song


@playlist.command()
@click.argument("playlist_id")
@click.argument("song_id")
def remove(playlist_id: str, song_id: str) -> None:
    """Remove a song from a playlist."""
    console.print(f"[yellow]Remove song from playlist not yet implemented[/yellow]")
    # TODO: Remove song


@cli.command()
@click.argument("song_id")
@click.option("--rating", "-r", type=click.IntRange(1, 5), help="Rating (1-5)")
@click.option("--notes", "-n", help="Notes about the performance")
def sung(song_id: str, rating: int | None, notes: str | None) -> None:
    """Mark a song as sung."""
    console.print(f"[yellow]Mark song '{song_id}' as sung not yet implemented[/yellow]")
    # TODO: Record sung


@cli.command()
def history() -> None:
    """Show songs I've sung."""
    console.print("[yellow]History not yet implemented[/yellow]")
    # TODO: Show history


def main() -> None:
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
