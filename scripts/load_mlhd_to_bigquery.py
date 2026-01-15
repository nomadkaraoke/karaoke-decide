#!/usr/bin/env python3
"""
Load MLHD+ co-occurrence data into BigQuery.

This script runs on the VM with GCS access to:
1. Convert cooccurrence.json to NDJSON and upload to GCS
2. Convert mbid_spotify_mapping.json to NDJSON and upload to GCS

Then run the BigQuery load commands from a machine with BQ access.

Usage:
    # On VM (convert and upload to GCS):
    python scripts/load_mlhd_to_bigquery.py prepare

    # From laptop (load to BigQuery):
    python scripts/load_mlhd_to_bigquery.py load
"""

import json
import sys
import tempfile
from pathlib import Path

from google.cloud import bigquery, storage
from rich.console import Console

console = Console()

PROJECT_ID = "nomadkaraoke"
DATASET_ID = "karaoke_decide"
GCS_BUCKET_MLHD = "nomadkaraoke-mlhd-data"
GCS_BUCKET_MB = "nomadkaraoke-musicbrainz-data"


def download_from_gcs(bucket_name: str, blob_name: str, local_path: Path) -> None:
    """Download a file from GCS."""
    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.download_to_filename(str(local_path))
    console.print(f"[green]Downloaded gs://{bucket_name}/{blob_name}[/green]")


def upload_to_gcs(local_path: Path, bucket_name: str, blob_name: str) -> None:
    """Upload a file to GCS."""
    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(str(local_path))
    console.print(f"[green]Uploaded to gs://{bucket_name}/{blob_name}[/green]")


def prepare_cooccurrence(json_path: Path, output_path: Path) -> int:
    """Convert co-occurrence JSON to NDJSON."""
    console.print("[blue]Converting co-occurrence data to NDJSON...[/blue]")

    with open(json_path) as f:
        data = json.load(f)

    count = 0
    with open(output_path, "w") as f:
        for key, stats in data.items():
            mbid_a, mbid_b = key.split("|")
            record = {
                "artist_a_mbid": mbid_a,
                "artist_b_mbid": mbid_b,
                "shared_users": stats["shared"],
                "users_a": stats["users_a"],
                "users_b": stats["users_b"],
                "jaccard_similarity": stats["jaccard"],
            }
            f.write(json.dumps(record) + "\n")
            count += 1

    console.print(f"[green]Converted {count:,} artist pairs[/green]")
    return count


def prepare_mapping(json_path: Path, output_path: Path) -> int:
    """Convert MBID-Spotify mapping JSON to NDJSON."""
    console.print("[blue]Converting MBID-Spotify mapping to NDJSON...[/blue]")

    with open(json_path) as f:
        data = json.load(f)

    count = 0
    with open(output_path, "w") as f:
        for mbid, info in data.items():
            if info.get("spotify_id"):
                record = {
                    "artist_mbid": mbid,
                    "spotify_artist_id": info["spotify_id"],
                    "artist_name": info.get("name"),
                }
                f.write(json.dumps(record) + "\n")
                count += 1

    console.print(f"[green]Converted {count:,} mappings[/green]")
    return count


def cmd_prepare():
    """Download JSONs, convert to NDJSON, upload to GCS."""
    console.print("[bold blue]MLHD+ Prepare for BigQuery[/bold blue]\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        # Download files from GCS
        cooccurrence_path = tmppath / "cooccurrence.json"
        download_from_gcs(GCS_BUCKET_MLHD, "cooccurrence.json", cooccurrence_path)

        mapping_path = tmppath / "mbid_spotify_mapping.json"
        download_from_gcs(GCS_BUCKET_MB, "processed/mbid_spotify_mapping.json", mapping_path)

        # Convert to NDJSON
        cooccurrence_ndjson = tmppath / "cooccurrence.ndjson"
        cooccurrence_count = prepare_cooccurrence(cooccurrence_path, cooccurrence_ndjson)

        mapping_ndjson = tmppath / "mbid_spotify_mapping.ndjson"
        mapping_count = prepare_mapping(mapping_path, mapping_ndjson)

        # Upload NDJSON to GCS
        console.print("\n[blue]Uploading NDJSON files to GCS...[/blue]")
        upload_to_gcs(cooccurrence_ndjson, GCS_BUCKET_MLHD, "cooccurrence.ndjson")
        upload_to_gcs(mapping_ndjson, GCS_BUCKET_MB, "processed/mbid_spotify_mapping.ndjson")

    console.print("\n" + "=" * 60)
    console.print("[bold green]PREPARE COMPLETE![/bold green]")
    console.print("=" * 60)
    console.print(f"Co-occurrence pairs: {cooccurrence_count:,}")
    console.print(f"MBID-Spotify mappings: {mapping_count:,}")
    console.print("\n[bold]NDJSON files ready in GCS:[/bold]")
    console.print(f"  - gs://{GCS_BUCKET_MLHD}/cooccurrence.ndjson")
    console.print(f"  - gs://{GCS_BUCKET_MB}/processed/mbid_spotify_mapping.ndjson")
    console.print("\n[bold yellow]Next step:[/bold yellow]")
    console.print("  Run 'python scripts/load_mlhd_to_bigquery.py load' from your laptop")


def cmd_load():
    """Load NDJSON from GCS into BigQuery and create similarity table."""
    console.print("[bold blue]MLHD+ Load to BigQuery[/bold blue]\n")

    bq_client = bigquery.Client(project=PROJECT_ID)

    # Load co-occurrence staging table
    console.print("[blue]Loading co-occurrence staging table...[/blue]")
    cooccurrence_schema = [
        bigquery.SchemaField("artist_a_mbid", "STRING"),
        bigquery.SchemaField("artist_b_mbid", "STRING"),
        bigquery.SchemaField("shared_users", "INT64"),
        bigquery.SchemaField("users_a", "INT64"),
        bigquery.SchemaField("users_b", "INT64"),
        bigquery.SchemaField("jaccard_similarity", "FLOAT64"),
    ]

    cooccurrence_table = f"{PROJECT_ID}.{DATASET_ID}.mlhd_cooccurrence_staging"
    job_config = bigquery.LoadJobConfig(
        schema=cooccurrence_schema,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )

    uri = f"gs://{GCS_BUCKET_MLHD}/cooccurrence.ndjson"
    job = bq_client.load_table_from_uri(uri, cooccurrence_table, job_config=job_config)
    job.result()

    table = bq_client.get_table(cooccurrence_table)
    console.print(f"[green]Loaded {table.num_rows:,} rows to {cooccurrence_table}[/green]")

    # Load MBID-Spotify mapping table
    console.print("[blue]Loading MBID-Spotify mapping table...[/blue]")
    mapping_schema = [
        bigquery.SchemaField("artist_mbid", "STRING"),
        bigquery.SchemaField("spotify_artist_id", "STRING"),
        bigquery.SchemaField("artist_name", "STRING"),
    ]

    mapping_table = f"{PROJECT_ID}.{DATASET_ID}.mbid_spotify_mapping"
    job_config = bigquery.LoadJobConfig(
        schema=mapping_schema,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )

    uri = f"gs://{GCS_BUCKET_MB}/processed/mbid_spotify_mapping.ndjson"
    job = bq_client.load_table_from_uri(uri, mapping_table, job_config=job_config)
    job.result()

    table = bq_client.get_table(mapping_table)
    console.print(f"[green]Loaded {table.num_rows:,} rows to {mapping_table}[/green]")

    # Create final similarity table
    console.print("[blue]Creating mlhd_artist_similarity table...[/blue]")
    sql = f"""
    CREATE OR REPLACE TABLE `{PROJECT_ID}.{DATASET_ID}.mlhd_artist_similarity` AS
    SELECT
        m1.spotify_artist_id AS artist_a_id,
        COALESCE(sa1.artist_name, m1.artist_name) AS artist_a_name,
        m2.spotify_artist_id AS artist_b_id,
        COALESCE(sa2.artist_name, m2.artist_name) AS artist_b_name,
        c.shared_users,
        c.users_a,
        c.users_b,
        c.jaccard_similarity
    FROM `{PROJECT_ID}.{DATASET_ID}.mlhd_cooccurrence_staging` c
    JOIN `{PROJECT_ID}.{DATASET_ID}.mbid_spotify_mapping` m1
        ON c.artist_a_mbid = m1.artist_mbid
    JOIN `{PROJECT_ID}.{DATASET_ID}.mbid_spotify_mapping` m2
        ON c.artist_b_mbid = m2.artist_mbid
    LEFT JOIN `{PROJECT_ID}.{DATASET_ID}.spotify_artists` sa1
        ON m1.spotify_artist_id = sa1.artist_id
    LEFT JOIN `{PROJECT_ID}.{DATASET_ID}.spotify_artists` sa2
        ON m2.spotify_artist_id = sa2.artist_id
    WHERE m1.spotify_artist_id IS NOT NULL
      AND m2.spotify_artist_id IS NOT NULL
    """

    job = bq_client.query(sql)
    job.result()

    similarity_table = f"{PROJECT_ID}.{DATASET_ID}.mlhd_artist_similarity"
    table = bq_client.get_table(similarity_table)
    console.print(f"[green]Created {similarity_table} with {table.num_rows:,} rows[/green]")

    # Verify data
    verify_data(bq_client)


def verify_data(bq_client: bigquery.Client) -> None:
    """Verify the data looks correct."""
    console.print("\n[blue]Verifying data...[/blue]")

    # Sample some similar artists
    sql = f"""
    SELECT
        artist_a_name,
        artist_b_name,
        shared_users,
        jaccard_similarity
    FROM `{PROJECT_ID}.{DATASET_ID}.mlhd_artist_similarity`
    WHERE artist_a_name IS NOT NULL AND artist_b_name IS NOT NULL
    ORDER BY shared_users DESC
    LIMIT 20
    """

    results = list(bq_client.query(sql).result())

    console.print("\n[bold]Top 20 artist pairs by shared users:[/bold]")
    for row in results:
        console.print(
            f"  {row.artist_a_name} <-> {row.artist_b_name}: "
            f"{row.shared_users:,} shared users, jaccard={row.jaccard_similarity:.4f}"
        )

    # Check a known artist
    console.print("\n[bold]Sample: Artists similar to Green Day:[/bold]")
    sample_sql = f"""
    SELECT
        CASE WHEN artist_a_name = 'Green Day' THEN artist_b_name ELSE artist_a_name END as similar_artist,
        shared_users,
        jaccard_similarity
    FROM `{PROJECT_ID}.{DATASET_ID}.mlhd_artist_similarity`
    WHERE artist_a_name = 'Green Day' OR artist_b_name = 'Green Day'
    ORDER BY shared_users DESC
    LIMIT 10
    """

    results = list(bq_client.query(sample_sql).result())
    for row in results:
        console.print(f"  {row.similar_artist}: {row.shared_users:,} shared, jaccard={row.jaccard_similarity:.4f}")

    console.print("\n" + "=" * 60)
    console.print("[bold green]LOAD COMPLETE![/bold green]")
    console.print("=" * 60)


def main():
    if len(sys.argv) < 2:
        console.print("Usage: python load_mlhd_to_bigquery.py <prepare|load>")
        console.print("  prepare - Convert JSON to NDJSON and upload to GCS (run on VM)")
        console.print("  load    - Load from GCS to BigQuery (run from laptop)")
        sys.exit(1)

    command = sys.argv[1]
    if command == "prepare":
        cmd_prepare()
    elif command == "load":
        cmd_load()
    else:
        console.print(f"[red]Unknown command: {command}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
