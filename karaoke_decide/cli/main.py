"""Main CLI entry point for Karaoke Decide."""

import click
from rich.console import Console
from rich.table import Table

from karaoke_decide import __version__
from karaoke_decide.services.bigquery_catalog import BigQueryCatalogService

console = Console()

# Lazy-loaded catalog service
_catalog_service = None


def get_catalog_service() -> BigQueryCatalogService:
    """Get or create the catalog service."""
    global _catalog_service
    if _catalog_service is None:
        _catalog_service = BigQueryCatalogService()
    return _catalog_service


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
@click.option("--limit", "-l", default=20, help="Number of results")
@click.option("--min-brands", "-b", default=0, help="Minimum brand count (popularity)")
def search(query: str, limit: int, min_brands: int) -> None:
    """Search the karaoke catalog by artist or title."""
    with console.status(f"Searching for '{query}'..."):
        svc = get_catalog_service()
        results = svc.search_songs(query, limit=limit, min_brands=min_brands)

    if not results:
        console.print(f"[yellow]No results found for '{query}'[/yellow]")
        return

    table = Table(title=f"Search Results: {query}")
    table.add_column("ID", style="dim")
    table.add_column("Artist", style="cyan")
    table.add_column("Title", style="green")
    table.add_column("Brands", justify="right", style="magenta")

    for song in results:
        table.add_row(
            str(song.id),
            song.artist,
            song.title,
            str(song.brand_count),
        )

    console.print(table)
    console.print(f"[dim]Found {len(results)} songs[/dim]")


@songs.command()
@click.argument("artist")
@click.option("--limit", "-l", default=20, help="Number of results")
def artist(artist: str, limit: int) -> None:
    """Show all songs by an artist."""
    with console.status(f"Searching for songs by '{artist}'..."):
        svc = get_catalog_service()
        results = svc.get_songs_by_artist(artist, limit=limit)

    if not results:
        console.print(f"[yellow]No songs found for artist '{artist}'[/yellow]")
        return

    table = Table(title=f"Songs by {artist}")
    table.add_column("ID", style="dim")
    table.add_column("Title", style="green")
    table.add_column("Brands", justify="right", style="magenta")

    for song in results:
        table.add_row(
            str(song.id),
            song.title,
            str(song.brand_count),
        )

    console.print(table)
    console.print(f"[dim]Found {len(results)} songs[/dim]")


@songs.command()
@click.option("--limit", "-l", default=50, help="Number of results")
@click.option("--min-brands", "-b", default=5, help="Minimum brand count")
def popular(limit: int, min_brands: int) -> None:
    """Show most popular karaoke songs."""
    with console.status("Fetching popular songs..."):
        svc = get_catalog_service()
        results = svc.get_popular_songs(limit=limit, min_brands=min_brands)

    if not results:
        console.print("[yellow]No popular songs found[/yellow]")
        return

    table = Table(title=f"Most Popular Karaoke Songs ({min_brands}+ brands)")
    table.add_column("#", style="dim", justify="right")
    table.add_column("Artist", style="cyan")
    table.add_column("Title", style="green")
    table.add_column("Brands", justify="right", style="magenta")

    for i, song in enumerate(results, 1):
        table.add_row(
            str(i),
            song.artist,
            song.title,
            str(song.brand_count),
        )

    console.print(table)


@songs.command()
def stats() -> None:
    """Show catalog statistics."""
    with console.status("Fetching stats..."):
        svc = get_catalog_service()
        stats = svc.get_stats()

    console.print("\n[bold]Karaoke Catalog Stats[/bold]")
    console.print(f"  Total songs:     [cyan]{stats['total_songs']:,}[/cyan]")
    console.print(f"  Unique artists:  [cyan]{stats['unique_artists']:,}[/cyan]")
    console.print(f"  Max brand count: [cyan]{stats['max_brand_count']}[/cyan]")
    console.print(f"  Avg brand count: [cyan]{stats['avg_brand_count']:.2f}[/cyan]")


@songs.command()
def browse() -> None:
    """Interactive browse with filters."""
    console.print("[yellow]Interactive browse not yet implemented[/yellow]")
    # TODO: Interactive browser


@songs.command()
def mine() -> None:
    """Show songs from my listening history."""
    console.print("[yellow]My songs not yet implemented - connect Spotify first[/yellow]")
    # TODO: Show matched songs


@songs.command()
def top() -> None:
    """Show my top karaoke recommendations."""
    console.print("[yellow]Top songs not yet implemented - connect Spotify first[/yellow]")
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
    console.print("[yellow]Add song to playlist not yet implemented[/yellow]")
    # TODO: Add song


@playlist.command()
@click.argument("playlist_id")
@click.argument("song_id")
def remove(playlist_id: str, song_id: str) -> None:
    """Remove a song from a playlist."""
    console.print("[yellow]Remove song from playlist not yet implemented[/yellow]")
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
