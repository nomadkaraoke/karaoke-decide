#!/usr/bin/env python3
"""
Migrate existing decide_users to include MBIDs.

This script backfills MusicBrainz IDs (MBIDs) for existing users who have
quiz_artists_known but no quiz_artist_mbids.

The migration:
1. Queries all users with quiz_artists_known but no quiz_artist_mbids
2. For each user, looks up MBIDs for their known artists
3. Updates the user document with quiz_artist_mbids array
4. Also enriches quiz_manual_artists with MBIDs where possible

Usage:
    # Preview (dry run)
    python scripts/migrate_users_to_mbid.py --dry-run

    # Run migration
    python scripts/migrate_users_to_mbid.py

    # Check migration status
    python scripts/migrate_users_to_mbid.py --status

    # Limit number of users (for testing)
    python scripts/migrate_users_to_mbid.py --limit 10
"""

import sys
from pathlib import Path

import click
from google.cloud import bigquery, firestore
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from karaoke_decide.services.bigquery_catalog import BigQueryCatalogService

# Configuration
PROJECT_ID = "nomadkaraoke"
COLLECTION = "decide_users"

console = Console()


def get_firestore_client() -> firestore.Client:
    """Get Firestore client."""
    return firestore.Client(project=PROJECT_ID)


def get_bigquery_client() -> bigquery.Client:
    """Get BigQuery client."""
    return bigquery.Client(project=PROJECT_ID)


def get_users_needing_migration(
    db: firestore.Client,
    limit: int | None = None,
) -> list[dict]:
    """Get users who have quiz_artists_known but no quiz_artist_mbids.

    Args:
        db: Firestore client
        limit: Optional limit on number of users

    Returns:
        List of user documents needing migration
    """
    # Query users with quiz_artists_known
    query = db.collection(COLLECTION).where(filter=firestore.FieldFilter("quiz_artists_known", "!=", []))

    if limit:
        query = query.limit(limit)

    users = []
    for doc in query.stream():
        data = doc.to_dict()
        data["_id"] = doc.id

        # Only include if quiz_artist_mbids field doesn't exist yet
        if "quiz_artist_mbids" not in data:
            users.append(data)

    return users


def get_migration_status(db: firestore.Client) -> dict:
    """Get overall migration status.

    Returns:
        Dict with counts of users in various states
    """
    # Count users with quiz data
    total_with_quiz = 0
    with_mbids = 0
    without_mbids = 0

    for doc in db.collection(COLLECTION).stream():
        data = doc.to_dict()
        if data.get("quiz_artists_known"):
            total_with_quiz += 1
            # Check if field exists (empty array counts as migrated)
            if "quiz_artist_mbids" in data:
                with_mbids += 1
            else:
                without_mbids += 1

    return {
        "total_with_quiz": total_with_quiz,
        "with_mbids": with_mbids,
        "without_mbids": without_mbids,
        "percent_migrated": (with_mbids / total_with_quiz * 100) if total_with_quiz > 0 else 0,
    }


def migrate_user(
    user: dict,
    catalog: BigQueryCatalogService,
    db: firestore.Client,
    dry_run: bool = False,
) -> tuple[bool, int, int]:
    """Migrate a single user to include MBIDs.

    Args:
        user: User document
        catalog: BigQuery catalog service for MBID lookups
        db: Firestore client
        dry_run: If True, don't actually update

    Returns:
        Tuple of (success, artists_count, mbids_resolved)
    """
    doc_id = user["_id"]
    known_artists = user.get("quiz_artists_known", [])
    manual_artists = user.get("quiz_manual_artists", [])

    if not known_artists and not manual_artists:
        return False, 0, 0

    # Resolve artist names to MBIDs
    mbid_map = {}
    if known_artists:
        try:
            mbid_map = catalog.lookup_mbids_by_names(known_artists)
        except Exception as e:
            console.print(f"[yellow]Warning: MBID lookup failed for {doc_id}: {e}[/yellow]")

    # Collect MBIDs from known artists
    artist_mbids = []
    for artist in known_artists:
        normalized = artist.lower().strip()
        mbid = mbid_map.get(normalized, "")
        if mbid:
            artist_mbids.append(mbid)

    # Enrich manual artists with MBIDs
    enriched_manual = []
    if manual_artists:
        for artist in manual_artists:
            enriched = dict(artist)  # Copy
            spotify_id = artist.get("artist_id")

            # Try to get MBID from Spotify ID mapping
            if spotify_id and not enriched.get("mbid"):
                try:
                    mbid = catalog.lookup_mbid_by_spotify_id(spotify_id)
                    if mbid:
                        enriched["mbid"] = mbid
                        artist_mbids.append(mbid)
                except Exception:
                    pass

            enriched_manual.append(enriched)

    # Remove duplicates while preserving order
    unique_mbids = list(dict.fromkeys(artist_mbids))

    if dry_run:
        return True, len(known_artists), len(unique_mbids)

    # Update Firestore - always write quiz_artist_mbids to mark as processed
    update_data = {
        "quiz_artist_mbids": unique_mbids,  # Empty array if no MBIDs resolved
    }
    if enriched_manual:
        update_data["quiz_manual_artists"] = enriched_manual

    try:
        db.collection(COLLECTION).document(doc_id).update(update_data)
        return True, len(known_artists), len(unique_mbids)
    except Exception as e:
        console.print(f"[red]Error updating {doc_id}: {e}[/red]")
        return False, len(known_artists), 0


@click.command()
@click.option("--dry-run", is_flag=True, help="Preview changes without updating")
@click.option("--limit", type=int, help="Limit number of users to migrate")
@click.option("--status", "show_status", is_flag=True, help="Show migration status")
def main(dry_run: bool, limit: int | None, show_status: bool):
    """Migrate existing users to include MBIDs."""
    db = get_firestore_client()
    bq = get_bigquery_client()
    catalog = BigQueryCatalogService(bq)

    if show_status:
        console.print("[bold]Migration Status[/bold]")
        status = get_migration_status(db)

        table = Table()
        table.add_column("Metric", style="cyan")
        table.add_column("Value", justify="right")

        table.add_row("Users with quiz data", f"{status['total_with_quiz']:,}")
        table.add_row("Already have MBIDs", f"{status['with_mbids']:,}")
        table.add_row("Need migration", f"{status['without_mbids']:,}")
        table.add_row("Percent complete", f"{status['percent_migrated']:.1f}%")

        console.print(table)
        return

    console.print("[bold]MBID User Migration[/bold]")
    if dry_run:
        console.print("[yellow]DRY RUN - No changes will be made[/yellow]")

    # Get users needing migration
    console.print("[blue]Finding users needing migration...[/blue]")
    users = get_users_needing_migration(db, limit=limit)

    if not users:
        console.print("[green]No users need migration![/green]")
        return

    console.print(f"[blue]Found {len(users)} users to migrate[/blue]")

    # Migrate users
    success_count = 0
    failed_count = 0
    total_artists = 0
    total_mbids = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        task = progress.add_task("Migrating users...", total=len(users))

        for user in users:
            success, artists, mbids = migrate_user(user, catalog, db, dry_run=dry_run)

            if success:
                success_count += 1
                total_artists += artists
                total_mbids += mbids
            else:
                failed_count += 1

            progress.advance(task)

    # Report results
    console.print()
    table = Table(title="Migration Results")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    table.add_row("Users processed", str(len(users)))
    table.add_row("Successful", f"[green]{success_count}[/green]")
    table.add_row("Failed", f"[red]{failed_count}[/red]" if failed_count else "0")
    table.add_row("Total artists", f"{total_artists:,}")
    table.add_row("MBIDs resolved", f"{total_mbids:,}")

    if total_artists > 0:
        coverage = total_mbids / total_artists * 100
        table.add_row("MBID coverage", f"{coverage:.1f}%")

    console.print(table)

    if dry_run:
        console.print()
        console.print("[yellow]This was a dry run. Run without --dry-run to apply changes.[/yellow]")


if __name__ == "__main__":
    main()
