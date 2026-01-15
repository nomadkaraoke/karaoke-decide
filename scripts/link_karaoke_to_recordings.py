#!/usr/bin/env python3
"""
Link Karaoke Catalog to MusicBrainz Recordings.

This script creates the karaoke_recording_links table by matching karaoke songs
to MusicBrainz recordings using multiple strategies:

1. ISRC Matching (Highest Confidence: 0.95)
   - Match karaoke song (artist + title) to Spotify track via name normalization
   - Get ISRC from Spotify track
   - Look up MB recording via ISRC

2. Exact Name Matching (Medium Confidence: 0.80)
   - Normalize artist + title from karaoke catalog
   - Match against MB recordings with same normalized artist_credit + title

3. Fuzzy Name Matching (Lower Confidence: 0.60)
   - For songs with no exact match, use similarity scoring (future enhancement)

Usage:
    python scripts/link_karaoke_to_recordings.py run
    python scripts/link_karaoke_to_recordings.py status
    python scripts/link_karaoke_to_recordings.py dry-run --limit 1000

Environment:
    - Requires google-cloud-bigquery
    - Requires mb_recordings and mb_recording_isrc tables to exist
"""

import json
import re
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

import click
from google.cloud import bigquery, storage
from rich.console import Console
from rich.table import Table

# Configuration
PROJECT_ID = "nomadkaraoke"
DATASET_ID = "karaoke_decide"
GCS_BUCKET = "nomadkaraoke-musicbrainz-data"
GCS_PROCESSED_PREFIX = "processed"

# Work directory
WORK_DIR = Path("/tmp/karaoke_linking")

console = Console()


def ensure_work_dir() -> Path:
    """Ensure work directory exists."""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    return WORK_DIR


def normalize_for_matching(text: str) -> str:
    """Normalize text for matching.

    Same normalization as BigQueryCatalogService for consistency.
    """
    if not text:
        return ""
    result = text.lower()
    result = re.sub(r"[^a-z0-9 ]", " ", result)
    result = re.sub(r"\s+", " ", result)
    return result.strip()


@dataclass
class KaraokeLink:
    """Link between karaoke song and MB recording."""

    karaoke_id: int
    recording_mbid: str | None
    spotify_track_id: str | None
    match_method: str
    match_confidence: float


def link_via_isrc(client: bigquery.Client, limit: int | None = None) -> Generator[KaraokeLink, None, None]:
    """Link karaoke songs to MB recordings via ISRC.

    Strategy:
    1. Join karaoke catalog with Spotify tracks on normalized name
    2. Get ISRC from Spotify track
    3. Look up MB recording via ISRC
    """
    console.print("[blue]Linking via ISRC (karaoke → Spotify → ISRC → MB)...[/blue]")

    # This query does the full chain in one go:
    # karaoke → normalized match → Spotify track → ISRC → MB recording
    limit_clause = f"LIMIT {limit}" if limit else ""

    sql = f"""
        WITH normalized_karaoke AS (
            SELECT
                k.Id AS karaoke_id,
                k.Artist AS karaoke_artist,
                k.Title AS karaoke_title,
                TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(k.Artist), r'[^a-z0-9 ]', ' '), r' +', ' ')) AS normalized_artist,
                TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(k.Title), r'[^a-z0-9 ]', ' '), r' +', ' ')) AS normalized_title
            FROM `{PROJECT_ID}.{DATASET_ID}.karaokenerds_raw` k
        ),
        spotify_matches AS (
            SELECT
                nk.karaoke_id,
                st.spotify_id,
                st.isrc,
                ROW_NUMBER() OVER (
                    PARTITION BY nk.karaoke_id
                    ORDER BY st.popularity DESC
                ) AS rn
            FROM normalized_karaoke nk
            JOIN `{PROJECT_ID}.{DATASET_ID}.spotify_tracks` st
                ON TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(st.artist_name), r'[^a-z0-9 ]', ' '), r' +', ' ')) = nk.normalized_artist
                AND TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(st.title), r'[^a-z0-9 ]', ' '), r' +', ' ')) = nk.normalized_title
            WHERE st.isrc IS NOT NULL
        ),
        mb_matches AS (
            SELECT
                sm.karaoke_id,
                sm.spotify_id AS spotify_track_id,
                ri.recording_mbid,
                ROW_NUMBER() OVER (
                    PARTITION BY sm.karaoke_id
                    ORDER BY sm.rn ASC
                ) AS final_rn
            FROM spotify_matches sm
            JOIN `{PROJECT_ID}.{DATASET_ID}.mb_recording_isrc` ri
                ON sm.isrc = ri.isrc
            WHERE sm.rn = 1
        )
        SELECT
            karaoke_id,
            recording_mbid,
            spotify_track_id
        FROM mb_matches
        WHERE final_rn = 1
        {limit_clause}
    """

    results = client.query(sql).result()

    count = 0
    for row in results:
        count += 1
        yield KaraokeLink(
            karaoke_id=row.karaoke_id,
            recording_mbid=row.recording_mbid,
            spotify_track_id=row.spotify_track_id,
            match_method="isrc",
            match_confidence=0.95,
        )

    console.print(f"[green]ISRC matching: {count:,} links[/green]")


def link_via_exact_name(
    client: bigquery.Client,
    exclude_karaoke_ids: set[int],
    limit: int | None = None,
) -> Generator[KaraokeLink, None, None]:
    """Link remaining karaoke songs via exact name matching to MB recordings.

    For songs that couldn't be matched via ISRC.
    """
    console.print("[blue]Linking via exact name matching (karaoke → MB)...[/blue]")

    # Skip songs we already matched
    if exclude_karaoke_ids:
        exclude_clause = f"AND k.Id NOT IN ({','.join(str(id) for id in exclude_karaoke_ids)})"
    else:
        exclude_clause = ""

    limit_clause = f"LIMIT {limit}" if limit else ""

    # Match directly to MB recordings via normalized artist_credit + title
    sql = f"""
        WITH normalized_karaoke AS (
            SELECT
                k.Id AS karaoke_id,
                TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(k.Artist), r'[^a-z0-9 ]', ' '), r' +', ' ')) AS normalized_artist,
                TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(k.Title), r'[^a-z0-9 ]', ' '), r' +', ' ')) AS normalized_title
            FROM `{PROJECT_ID}.{DATASET_ID}.karaokenerds_raw` k
            WHERE 1=1 {exclude_clause}
        ),
        mb_matches AS (
            SELECT
                nk.karaoke_id,
                r.recording_mbid,
                ROW_NUMBER() OVER (
                    PARTITION BY nk.karaoke_id
                    ORDER BY r.recording_mbid
                ) AS rn
            FROM normalized_karaoke nk
            JOIN `{PROJECT_ID}.{DATASET_ID}.mb_recordings` r
                ON r.name_normalized = nk.normalized_title
                AND TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(r.artist_credit), r'[^a-z0-9 ]', ' '), r' +', ' ')) = nk.normalized_artist
        )
        SELECT
            karaoke_id,
            recording_mbid
        FROM mb_matches
        WHERE rn = 1
        {limit_clause}
    """

    results = client.query(sql).result()

    count = 0
    for row in results:
        count += 1
        yield KaraokeLink(
            karaoke_id=row.karaoke_id,
            recording_mbid=row.recording_mbid,
            spotify_track_id=None,
            match_method="exact_name",
            match_confidence=0.80,
        )

    console.print(f"[green]Exact name matching: {count:,} links[/green]")


def write_links_to_ndjson(links: list[KaraokeLink], output_path: Path) -> int:
    """Write links to NDJSON file."""
    count = 0
    with open(output_path, "w", encoding="utf-8") as f:
        for link in links:
            record = {
                "karaoke_id": link.karaoke_id,
                "recording_mbid": link.recording_mbid,
                "spotify_track_id": link.spotify_track_id,
                "match_method": link.match_method,
                "match_confidence": link.match_confidence,
            }
            f.write(json.dumps(record) + "\n")
            count += 1

    console.print(f"[green]Wrote {count:,} links to {output_path}[/green]")
    return count


def upload_to_gcs(local_path: Path, gcs_path: str) -> str:
    """Upload file to GCS."""
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(GCS_BUCKET)
    blob = bucket.blob(gcs_path)

    console.print(f"[blue]Uploading to gs://{GCS_BUCKET}/{gcs_path}...[/blue]")
    blob.upload_from_filename(str(local_path))

    return f"gs://{GCS_BUCKET}/{gcs_path}"


def load_to_bigquery(gcs_uri: str) -> None:
    """Load NDJSON from GCS to BigQuery."""
    client = bigquery.Client(project=PROJECT_ID)
    table_id = f"{PROJECT_ID}.{DATASET_ID}.karaoke_recording_links"

    schema = [
        bigquery.SchemaField("karaoke_id", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("recording_mbid", "STRING"),
        bigquery.SchemaField("spotify_track_id", "STRING"),
        bigquery.SchemaField("match_method", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("match_confidence", "FLOAT64"),
    ]

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        schema=schema,
        write_disposition="WRITE_TRUNCATE",
    )

    console.print(f"[blue]Loading to {table_id}...[/blue]")

    load_job = client.load_table_from_uri(gcs_uri, table_id, job_config=job_config)
    load_job.result()

    table = client.get_table(table_id)
    console.print(f"[green]Loaded {table.num_rows:,} rows to {table_id}[/green]")


# CLI Commands


@click.group()
def cli():
    """Link Karaoke Catalog to MusicBrainz Recordings."""
    pass


@cli.command()
@click.option("--limit", type=int, help="Limit number of links per method")
def run(limit: int | None):
    """Run full linking pipeline."""
    console.print("[bold blue]Starting Karaoke-Recording Linking Pipeline[/bold blue]")
    console.print()

    work_dir = ensure_work_dir()
    client = bigquery.Client(project=PROJECT_ID)

    # Step 1: ISRC-based linking
    console.print("[bold]Step 1: ISRC-based linking...[/bold]")
    isrc_links = list(link_via_isrc(client, limit=limit))
    isrc_ids = {link.karaoke_id for link in isrc_links}
    console.print()

    # Step 2: Exact name matching for remaining songs
    console.print("[bold]Step 2: Exact name matching...[/bold]")
    name_links = list(link_via_exact_name(client, exclude_karaoke_ids=isrc_ids, limit=limit))
    console.print()

    # Combine all links
    all_links = isrc_links + name_links

    console.print(f"[bold]Total links: {len(all_links):,}[/bold]")
    console.print(f"  ISRC matches: {len(isrc_links):,}")
    console.print(f"  Name matches: {len(name_links):,}")
    console.print()

    # Step 3: Write to NDJSON
    console.print("[bold]Step 3: Writing to NDJSON...[/bold]")
    output_path = work_dir / "karaoke_recording_links.ndjson"
    write_links_to_ndjson(all_links, output_path)
    console.print()

    # Step 4: Upload to GCS and load to BigQuery
    console.print("[bold]Step 4: Loading to BigQuery...[/bold]")
    gcs_uri = upload_to_gcs(output_path, f"{GCS_PROCESSED_PREFIX}/karaoke_recording_links.ndjson")
    load_to_bigquery(gcs_uri)
    console.print()

    console.print("[bold green]Linking Pipeline Complete![/bold green]")


@cli.command("dry-run")
@click.option("--limit", type=int, default=100, help="Limit number of links to preview")
def dry_run(limit: int):
    """Preview linking without writing to BigQuery."""
    console.print(f"[bold blue]Dry Run - Preview {limit} links[/bold blue]")
    console.print()

    client = bigquery.Client(project=PROJECT_ID)

    # Preview ISRC links
    console.print("[bold]ISRC-based matches (sample):[/bold]")
    isrc_sample = list(link_via_isrc(client, limit=limit))
    for link in isrc_sample[:5]:
        console.print(f"  #{link.karaoke_id} → {link.recording_mbid} (spotify: {link.spotify_track_id})")

    console.print()

    # Preview name matches
    console.print("[bold]Name-based matches (sample):[/bold]")
    name_sample = list(link_via_exact_name(client, exclude_karaoke_ids=set(), limit=limit))
    for link in name_sample[:5]:
        console.print(f"  #{link.karaoke_id} → {link.recording_mbid}")


@cli.command()
def status():
    """Check status of karaoke recording links."""
    client = bigquery.Client(project=PROJECT_ID)

    # Check if table exists
    table_id = f"{PROJECT_ID}.{DATASET_ID}.karaoke_recording_links"
    try:
        table = client.get_table(table_id)
        console.print(f"[green]Table exists:[/green] {table_id}")
        console.print(f"  Rows: {table.num_rows:,}")
    except Exception:
        console.print(f"[red]Table missing:[/red] {table_id}")
        return

    # Get breakdown by match method
    sql = f"""
        SELECT
            match_method,
            COUNT(*) as count,
            AVG(match_confidence) as avg_confidence
        FROM `{table_id}`
        GROUP BY match_method
        ORDER BY count DESC
    """

    results = client.query(sql).result()

    table_display = Table(title="Karaoke-Recording Links by Method")
    table_display.add_column("Method", style="cyan")
    table_display.add_column("Count", justify="right")
    table_display.add_column("Avg Confidence", justify="right")

    for row in results:
        table_display.add_row(
            row.match_method,
            f"{row.count:,}",
            f"{row.avg_confidence:.2f}",
        )

    console.print(table_display)

    # Get total karaoke songs for coverage calculation
    sql_total = f"""
        SELECT COUNT(*) as total
        FROM `{PROJECT_ID}.{DATASET_ID}.karaokenerds_raw`
    """
    total_karaoke = list(client.query(sql_total).result())[0].total

    coverage = table.num_rows / total_karaoke * 100
    console.print(f"\n[bold]Coverage:[/bold] {table.num_rows:,} / {total_karaoke:,} ({coverage:.1f}%)")


if __name__ == "__main__":
    cli()
