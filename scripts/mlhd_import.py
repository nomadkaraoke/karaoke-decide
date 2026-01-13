#!/usr/bin/env python3
"""
MLHD+ Data Import Script

Processes the MLHD+ (Music Listening Histories Dataset+) to extract artist
co-occurrence data for collaborative filtering recommendations.

Dataset: https://data.musicbrainz.org/pub/musicbrainz/listenbrainz/mlhd/
Format: Tab-separated files, one per user
  <timestamp>\t<artist_mbid>\t<release_mbid>\t<recording_mbid>

Usage:
    python scripts/mlhd_import.py download --output-dir ./data/mlhd
    python scripts/mlhd_import.py process --input-dir ./data/mlhd --output ./data/cooccurrence.parquet
    python scripts/mlhd_import.py map-spotify --input ./data/cooccurrence.parquet --output ./data/mapped.parquet
"""

import asyncio
import json
import tarfile
import tempfile
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import click
import httpx
import zstandard as zstd
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

console = Console()

# MLHD+ download URLs
MLHD_BASE_URL = "https://data.musicbrainz.org/pub/musicbrainz/listenbrainz/mlhd"
MLHD_COMPLETE_ARCHIVES = [f"mlhdplus-complete-{hex(i)[2:]}.tar" for i in range(16)]
MLHD_PARTIAL_ARCHIVES = [f"mlhdplus-partial-{hex(i)[2:]}.tar" for i in range(16)]

# ListenBrainz Labs API for Spotify mapping
LISTENBRAINZ_LABS_URL = "https://labs.api.listenbrainz.org"


@dataclass
class UserListeningHistory:
    """Extracted listening history for one user."""

    user_id: str
    artist_mbids: set[str]
    artist_play_counts: dict[str, int]
    total_listens: int


def extract_user_artists(filepath: Path) -> UserListeningHistory | None:
    """
    Extract unique artists and play counts from a single user file.

    Args:
        filepath: Path to .txt.zst file

    Returns:
        UserListeningHistory with artist data, or None if no valid data
    """
    user_id = filepath.stem.replace(".txt", "")
    artist_counts: dict[str, int] = defaultdict(int)
    total_listens = 0

    try:
        dctx = zstd.ZstdDecompressor()
        with open(filepath, "rb") as fh:
            with dctx.stream_reader(fh) as reader:
                text_reader = reader.read().decode("utf-8")
                for line in text_reader.strip().split("\n"):
                    if not line:
                        continue
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        # Artist MBID is second column (may contain commas for multi-artist)
                        artist_mbids_str = parts[1].strip()
                        if artist_mbids_str:
                            # Handle multi-artist recordings
                            for mbid in artist_mbids_str.split(","):
                                mbid = mbid.strip()
                                if mbid:
                                    artist_counts[mbid] += 1
                                    total_listens += 1
    except Exception as e:
        console.print(f"[red]Error processing {filepath}: {e}[/red]")
        return None

    if not artist_counts:
        return None

    return UserListeningHistory(
        user_id=user_id,
        artist_mbids=set(artist_counts.keys()),
        artist_play_counts=dict(artist_counts),
        total_listens=total_listens,
    )


def process_tar_archive(
    archive_path: Path,
    output_dir: Path,
    min_artists: int = 5,
    max_workers: int = 4,
) -> list[UserListeningHistory]:
    """
    Process a single tar archive and extract user listening histories.

    Args:
        archive_path: Path to .tar archive
        output_dir: Directory to extract files temporarily
        min_artists: Minimum number of artists to include user
        max_workers: Number of parallel workers for processing

    Returns:
        List of UserListeningHistory objects
    """
    histories = []

    with tempfile.TemporaryDirectory(dir=output_dir) as tmpdir:
        tmppath = Path(tmpdir)

        # Extract tar archive
        console.print(f"[blue]Extracting {archive_path.name}...[/blue]")
        with tarfile.open(archive_path, "r") as tar:
            tar.extractall(tmppath)

        # Find all .zst files
        zst_files = list(tmppath.rglob("*.txt.zst"))
        console.print(f"[green]Found {len(zst_files)} user files[/green]")

        # Process in parallel
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(f"Processing {archive_path.name}", total=len(zst_files))

            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                for result in executor.map(extract_user_artists, zst_files):
                    progress.advance(task)
                    if result and len(result.artist_mbids) >= min_artists:
                        histories.append(result)

    return histories


def build_cooccurrence_matrix(
    histories: list[UserListeningHistory],
    min_shared_users: int = 10,
) -> dict[tuple[str, str], dict]:
    """
    Build artist co-occurrence matrix from user histories.

    Args:
        histories: List of user listening histories
        min_shared_users: Minimum shared users to include pair

    Returns:
        Dict mapping (artist_a, artist_b) to co-occurrence stats
    """
    console.print("[blue]Building co-occurrence matrix...[/blue]")

    # Count co-occurrences
    cooccurrence: dict[tuple[str, str], int] = defaultdict(int)
    artist_user_counts: dict[str, int] = defaultdict(int)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Counting co-occurrences", total=len(histories))

        for history in histories:
            # Count users per artist
            for artist in history.artist_mbids:
                artist_user_counts[artist] += 1

            # Count co-occurrences (only for artists in this user's history)
            artists = sorted(history.artist_mbids)
            for i, artist_a in enumerate(artists):
                for artist_b in artists[i + 1 :]:
                    cooccurrence[(artist_a, artist_b)] += 1

            progress.advance(task)

    # Filter and compute stats
    console.print(f"[blue]Filtering {len(cooccurrence)} pairs...[/blue]")
    result = {}
    for (artist_a, artist_b), shared in cooccurrence.items():
        if shared >= min_shared_users:
            users_a = artist_user_counts[artist_a]
            users_b = artist_user_counts[artist_b]
            # Jaccard similarity: intersection / union
            jaccard = shared / (users_a + users_b - shared)
            result[(artist_a, artist_b)] = {
                "shared_users": shared,
                "users_a": users_a,
                "users_b": users_b,
                "jaccard_similarity": jaccard,
            }

    console.print(f"[green]Kept {len(result)} pairs with {min_shared_users}+ shared users[/green]")
    return result


async def map_mbid_to_spotify_via_musicbrainz(
    artist_mbids: list[str],
    rate_limit_delay: float = 1.1,  # MusicBrainz rate limit: 1 req/sec
) -> dict[str, dict]:
    """
    Map MusicBrainz artist IDs to Spotify artist IDs using MusicBrainz API.

    Queries MusicBrainz for URL relations which include Spotify artist links.

    Args:
        artist_mbids: List of MusicBrainz artist IDs
        rate_limit_delay: Delay between requests (MusicBrainz allows 1 req/sec)

    Returns:
        Dict mapping MBID to {spotify_id, name, type} or None if not found
    """
    console.print(f"[blue]Mapping {len(artist_mbids)} MBIDs to Spotify IDs via MusicBrainz API...[/blue]")
    console.print(f"[yellow]Note: Rate limited to 1 req/sec. ETA: {len(artist_mbids) / 60:.1f} minutes[/yellow]")

    mapping: dict[str, dict] = {}
    errors = 0

    async with httpx.AsyncClient(
        timeout=30.0,
        headers={"User-Agent": "KaraokeDecide/1.0 (contact@nomadkaraoke.com)"},
    ) as client:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Mapping to Spotify", total=len(artist_mbids))

            for mbid in artist_mbids:
                try:
                    response = await client.get(
                        f"https://musicbrainz.org/ws/2/artist/{mbid}",
                        params={"fmt": "json", "inc": "url-rels"},
                    )

                    if response.status_code == 200:
                        data = response.json()
                        artist_name = data.get("name")
                        artist_type = data.get("type")

                        # Look for Spotify URL in relations
                        spotify_id = None
                        for rel in data.get("relations", []):
                            url = rel.get("url", {}).get("resource", "")
                            if "open.spotify.com/artist/" in url:
                                spotify_id = url.split("/artist/")[-1].split("?")[0]
                                break

                        mapping[mbid] = {
                            "spotify_id": spotify_id,
                            "name": artist_name,
                            "type": artist_type,
                        }
                    elif response.status_code == 404:
                        mapping[mbid] = {"spotify_id": None, "name": None, "type": None}
                    else:
                        errors += 1

                except Exception as e:
                    console.print(f"[yellow]Error for {mbid}: {e}[/yellow]")
                    errors += 1

                progress.advance(task)

                # Respect rate limit
                await asyncio.sleep(rate_limit_delay)

    # Report stats
    mapped_count = sum(1 for v in mapping.values() if v.get("spotify_id"))
    console.print(
        f"[green]Mapped {mapped_count}/{len(artist_mbids)} "
        f"({100 * mapped_count / len(artist_mbids):.1f}%) to Spotify IDs[/green]"
    )
    if errors:
        console.print(f"[yellow]Encountered {errors} errors[/yellow]")

    return mapping


async def map_mbid_to_spotify_batch(
    artist_mbids: list[str],
    our_spotify_artists: dict[str, str] | None = None,
) -> dict[str, str | None]:
    """
    Fast batch mapping using name matching against our Spotify catalog.

    This is much faster than MusicBrainz API calls but less accurate.

    Args:
        artist_mbids: List of MusicBrainz artist IDs
        our_spotify_artists: Dict of lowercase name -> Spotify ID from our catalog

    Returns:
        Dict mapping MBID to Spotify artist ID or None
    """
    if not our_spotify_artists:
        console.print("[yellow]No Spotify catalog provided, falling back to API mapping[/yellow]")
        mb_mapping = await map_mbid_to_spotify_via_musicbrainz(artist_mbids)
        return {mbid: data.get("spotify_id") for mbid, data in mb_mapping.items()}

    console.print(f"[blue]Mapping {len(artist_mbids)} MBIDs via name matching...[/blue]")

    # First get artist names from MusicBrainz (batch via similar-artists is faster)
    # For now, fall back to individual lookups
    mb_mapping = await map_mbid_to_spotify_via_musicbrainz(artist_mbids[:100])  # Sample

    # Try name matching
    mapping: dict[str, str | None] = {}
    for mbid, data in mb_mapping.items():
        name = data.get("name", "").lower()
        mapping[mbid] = our_spotify_artists.get(name)

    return mapping


@click.group()
def cli():
    """MLHD+ Data Import Tool"""
    pass


@cli.command()
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    default=Path("./data/mlhd"),
    help="Directory to download archives to",
)
@click.option(
    "--archive-type",
    type=click.Choice(["complete", "partial", "both"]),
    default="complete",
    help="Which archive type to download",
)
@click.option(
    "--archives",
    type=str,
    default=None,
    help="Specific archives to download (e.g., '0,1,2' for first 3)",
)
def download(output_dir: Path, archive_type: str, archives: str | None):
    """Download MLHD+ archives from MusicBrainz."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Determine which archives to download
    to_download = []
    if archive_type in ("complete", "both"):
        to_download.extend(MLHD_COMPLETE_ARCHIVES)
    if archive_type in ("partial", "both"):
        to_download.extend(MLHD_PARTIAL_ARCHIVES)

    if archives:
        indices = [int(x.strip()) for x in archives.split(",")]
        to_download = [to_download[i] for i in indices if i < len(to_download)]

    console.print(f"[blue]Downloading {len(to_download)} archives to {output_dir}[/blue]")

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        for archive_name in to_download:
            url = f"{MLHD_BASE_URL}/{archive_name}"
            output_path = output_dir / archive_name

            if output_path.exists():
                console.print(f"[yellow]Skipping {archive_name} (already exists)[/yellow]")
                continue

            # Get file size
            with httpx.Client() as client:
                head = client.head(url)
                total_size = int(head.headers.get("content-length", 0))

            task = progress.add_task(archive_name, total=total_size)

            # Download with progress
            with httpx.Client() as client:
                with client.stream("GET", url) as response:
                    with open(output_path, "wb") as f:
                        for chunk in response.iter_bytes(chunk_size=8192):
                            f.write(chunk)
                            progress.advance(task, len(chunk))

            console.print(f"[green]Downloaded {archive_name}[/green]")


@cli.command()
@click.option(
    "--input-dir",
    type=click.Path(path_type=Path, exists=True),
    required=True,
    help="Directory containing MLHD+ archives",
)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    required=True,
    help="Output file for co-occurrence data (JSON)",
)
@click.option(
    "--min-artists",
    type=int,
    default=10,
    help="Minimum artists per user to include",
)
@click.option(
    "--min-shared-users",
    type=int,
    default=50,
    help="Minimum shared users for artist pair",
)
@click.option(
    "--max-workers",
    type=int,
    default=4,
    help="Parallel workers for processing",
)
@click.option(
    "--archive-limit",
    type=int,
    default=None,
    help="Limit number of archives to process (for testing)",
)
def process(
    input_dir: Path,
    output: Path,
    min_artists: int,
    min_shared_users: int,
    max_workers: int,
    archive_limit: int | None,
):
    """Process MLHD+ archives to build co-occurrence matrix."""
    # Find archives
    archives = sorted(input_dir.glob("mlhdplus-*.tar"))
    if archive_limit:
        archives = archives[:archive_limit]

    console.print(f"[blue]Processing {len(archives)} archives[/blue]")

    # Process each archive
    all_histories: list[UserListeningHistory] = []
    for archive_path in archives:
        histories = process_tar_archive(
            archive_path,
            input_dir,
            min_artists=min_artists,
            max_workers=max_workers,
        )
        all_histories.extend(histories)
        console.print(f"[green]Processed {archive_path.name}: {len(histories)} users[/green]")

    console.print(f"[blue]Total users: {len(all_histories)}[/blue]")

    # Build co-occurrence matrix
    cooccurrence = build_cooccurrence_matrix(all_histories, min_shared_users=min_shared_users)

    # Save results
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        # Convert tuple keys to strings for JSON
        json_data = {f"{a}|{b}": stats for (a, b), stats in cooccurrence.items()}
        json.dump(json_data, f)

    console.print(f"[green]Saved {len(cooccurrence)} artist pairs to {output}[/green]")

    # Print summary statistics
    table = Table(title="Co-occurrence Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Total users processed", str(len(all_histories)))
    table.add_row("Artist pairs", str(len(cooccurrence)))
    table.add_row(
        "Avg shared users",
        f"{sum(s['shared_users'] for s in cooccurrence.values()) / len(cooccurrence):.1f}" if cooccurrence else "N/A",
    )
    console.print(table)


@cli.command()
@click.option(
    "--input",
    "input_file",
    type=click.Path(path_type=Path, exists=True),
    required=True,
    help="Co-occurrence JSON file",
)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    required=True,
    help="Output file with Spotify mappings",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Limit number of MBIDs to map (for testing)",
)
def map_spotify(input_file: Path, output: Path, limit: int | None):
    """Map MusicBrainz IDs to Spotify IDs via MusicBrainz API."""
    # Load co-occurrence data
    with open(input_file) as f:
        cooccurrence = json.load(f)

    # Extract unique MBIDs
    all_mbids = set()
    for key in cooccurrence.keys():
        a, b = key.split("|")
        all_mbids.add(a)
        all_mbids.add(b)

    mbid_list = list(all_mbids)
    if limit:
        mbid_list = mbid_list[:limit]

    console.print(f"[blue]Found {len(all_mbids)} unique artist MBIDs, mapping {len(mbid_list)}[/blue]")

    # Map to Spotify via MusicBrainz API
    mapping = asyncio.run(map_mbid_to_spotify_via_musicbrainz(mbid_list))

    # Save mapping
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        json.dump(mapping, f, indent=2)

    console.print(f"[green]Saved mapping to {output}[/green]")


@cli.command()
@click.option(
    "--sample-file",
    type=click.Path(path_type=Path, exists=True),
    required=True,
    help="Path to a sample .txt.zst user file",
)
def inspect(sample_file: Path):
    """Inspect a sample user file to understand the data format."""
    result = extract_user_artists(sample_file)

    if not result:
        console.print("[red]Failed to extract data from file[/red]")
        return

    table = Table(title=f"User: {result.user_id}")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Total listens", str(result.total_listens))
    table.add_row("Unique artists", str(len(result.artist_mbids)))

    console.print(table)

    # Show top 10 artists by play count
    top_artists = sorted(result.artist_play_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    console.print("\n[bold]Top 10 Artists by Play Count:[/bold]")
    for mbid, count in top_artists:
        console.print(f"  {mbid}: {count} plays")


if __name__ == "__main__":
    cli()
