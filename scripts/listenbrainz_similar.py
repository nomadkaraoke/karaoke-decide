#!/usr/bin/env python3
"""
ListenBrainz Similar Artists Integration

Uses the ListenBrainz Labs API to get pre-computed similar artists,
avoiding the need to process MLHD+ data ourselves.

Usage:
    python scripts/listenbrainz_similar.py lookup --artist "Green Day"
    python scripts/listenbrainz_similar.py lookup --mbid "8f6bd1e4-fbe1-4f50-aa9b-94c450ec0f11"
    python scripts/listenbrainz_similar.py batch --input artists.txt --output similar.json
"""

import asyncio
import json
from pathlib import Path

import click
import httpx
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Table

console = Console()

LISTENBRAINZ_LABS_URL = "https://labs.api.listenbrainz.org"
MUSICBRAINZ_API_URL = "https://musicbrainz.org/ws/2"

# Best algorithm based on ListenBrainz docs
DEFAULT_ALGORITHM = "session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30"


async def search_artist_mbid(artist_name: str) -> dict | None:
    """Search MusicBrainz for an artist by name, return MBID and info."""
    async with httpx.AsyncClient(
        timeout=30.0,
        headers={"User-Agent": "KaraokeDecide/1.0 (contact@nomadkaraoke.com)"},
    ) as client:
        response = await client.get(
            f"{MUSICBRAINZ_API_URL}/artist",
            params={"query": artist_name, "fmt": "json", "limit": 5},
        )
        if response.status_code == 200:
            data = response.json()
            artists = data.get("artists", [])
            if artists:
                # Return best match
                artist = artists[0]
                return {
                    "mbid": artist.get("id"),
                    "name": artist.get("name"),
                    "type": artist.get("type"),
                    "score": artist.get("score"),
                    "disambiguation": artist.get("disambiguation"),
                }
    return None


async def get_similar_artists(
    artist_mbid: str,
    algorithm: str = DEFAULT_ALGORITHM,
    limit: int = 50,
) -> list[dict]:
    """
    Get similar artists from ListenBrainz Labs API.

    Args:
        artist_mbid: MusicBrainz artist ID
        algorithm: Similarity algorithm to use
        limit: Max results to return

    Returns:
        List of similar artists with scores
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{LISTENBRAINZ_LABS_URL}/similar-artists/json",
            params={
                "artist_mbids": artist_mbid,
                "algorithm": algorithm,
            },
        )
        if response.status_code == 200:
            results = response.json()
            # Filter out the seed artist and limit results
            similar = [r for r in results if r.get("artist_mbid") != artist_mbid][:limit]
            return similar
    return []


async def get_artist_spotify_id(artist_mbid: str) -> str | None:
    """Get Spotify artist ID from MusicBrainz URL relations."""
    async with httpx.AsyncClient(
        timeout=30.0,
        headers={"User-Agent": "KaraokeDecide/1.0 (contact@nomadkaraoke.com)"},
    ) as client:
        response = await client.get(
            f"{MUSICBRAINZ_API_URL}/artist/{artist_mbid}",
            params={"fmt": "json", "inc": "url-rels"},
        )
        if response.status_code == 200:
            data = response.json()
            for rel in data.get("relations", []):
                url = rel.get("url", {}).get("resource", "")
                if "open.spotify.com/artist/" in url:
                    return url.split("/artist/")[-1].split("?")[0]
    return None


async def get_similar_with_spotify_ids(
    artist_mbid: str,
    limit: int = 20,
) -> list[dict]:
    """
    Get similar artists with Spotify IDs resolved.

    This is the main function for integration with our recommendation system.
    """
    # Get similar artists from ListenBrainz
    similar = await get_similar_artists(artist_mbid, limit=limit * 2)  # Get extra for filtering

    if not similar:
        return []

    # Resolve Spotify IDs (rate limited, so we batch carefully)
    results = []
    for artist in similar:
        if len(results) >= limit:
            break

        mbid = artist.get("artist_mbid")
        spotify_id = await get_artist_spotify_id(mbid)

        if spotify_id:
            results.append(
                {
                    "mbid": mbid,
                    "spotify_id": spotify_id,
                    "name": artist.get("name"),
                    "score": artist.get("score"),
                }
            )

        # Rate limit for MusicBrainz
        await asyncio.sleep(1.1)

    return results


@click.group()
def cli():
    """ListenBrainz Similar Artists Tool"""
    pass


@cli.command()
@click.option("--artist", type=str, help="Artist name to search")
@click.option("--mbid", type=str, help="MusicBrainz artist ID")
@click.option("--limit", type=int, default=20, help="Number of similar artists")
@click.option("--with-spotify", is_flag=True, help="Also resolve Spotify IDs (slower)")
def lookup(artist: str | None, mbid: str | None, limit: int, with_spotify: bool):
    """Look up similar artists for a given artist."""
    if not artist and not mbid:
        console.print("[red]Provide either --artist or --mbid[/red]")
        return

    async def _lookup():
        # Get MBID if we have a name
        if artist and not mbid:
            console.print(f"[blue]Searching for '{artist}'...[/blue]")
            result = await search_artist_mbid(artist)
            if not result:
                console.print(f"[red]Artist '{artist}' not found[/red]")
                return
            artist_mbid = result["mbid"]
            console.print(f"[green]Found: {result['name']} ({result.get('type', 'Unknown')})[/green]")
            if result.get("disambiguation"):
                console.print(f"[dim]  {result['disambiguation']}[/dim]")
            console.print(f"[dim]  MBID: {artist_mbid}[/dim]")
        else:
            artist_mbid = mbid

        console.print()

        if with_spotify:
            console.print("[blue]Getting similar artists with Spotify IDs (this takes a while)...[/blue]")
            similar = await get_similar_with_spotify_ids(artist_mbid, limit=limit)

            table = Table(title="Similar Artists (with Spotify IDs)")
            table.add_column("Name", style="cyan")
            table.add_column("Score", style="green")
            table.add_column("Spotify ID", style="yellow")

            for s in similar:
                table.add_row(s["name"], str(s["score"]), s["spotify_id"])

            console.print(table)
        else:
            console.print("[blue]Getting similar artists...[/blue]")
            similar = await get_similar_artists(artist_mbid, limit=limit)

            table = Table(title="Similar Artists")
            table.add_column("Name", style="cyan")
            table.add_column("Type", style="dim")
            table.add_column("Score", style="green")
            table.add_column("MBID", style="dim")

            for s in similar:
                table.add_row(
                    s.get("name", "Unknown"),
                    s.get("type") or "-",
                    str(s.get("score", 0)),
                    s.get("artist_mbid", "")[:20] + "...",
                )

            console.print(table)

        console.print(f"\n[green]Found {len(similar)} similar artists[/green]")

    asyncio.run(_lookup())


@cli.command()
@click.option(
    "--input",
    "input_file",
    type=click.Path(path_type=Path, exists=True),
    required=True,
    help="File with artist names or MBIDs (one per line)",
)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    required=True,
    help="Output JSON file",
)
@click.option("--limit", type=int, default=20, help="Similar artists per input")
def batch(input_file: Path, output: Path, limit: int):
    """Process multiple artists in batch."""

    async def _batch():
        # Load input
        with open(input_file) as f:
            artists = [line.strip() for line in f if line.strip()]

        console.print(f"[blue]Processing {len(artists)} artists...[/blue]")

        results = {}

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Processing", total=len(artists))

            for artist in artists:
                # Check if it's an MBID or name
                if len(artist) == 36 and "-" in artist:
                    mbid = artist
                    name = None
                else:
                    # Search for MBID
                    result = await search_artist_mbid(artist)
                    if result:
                        mbid = result["mbid"]
                        name = result["name"]
                    else:
                        progress.advance(task)
                        continue

                # Get similar artists
                similar = await get_similar_artists(mbid, limit=limit)
                results[mbid] = {
                    "name": name or artist,
                    "similar": similar,
                }

                progress.advance(task)

                # Rate limit
                await asyncio.sleep(0.5)

        # Save results
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w") as f:
            json.dump(results, f, indent=2)

        console.print(f"[green]Saved results for {len(results)} artists to {output}[/green]")

    asyncio.run(_batch())


if __name__ == "__main__":
    cli()
