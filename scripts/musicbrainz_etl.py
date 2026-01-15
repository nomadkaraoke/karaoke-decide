#!/usr/bin/env python3
"""
MusicBrainz ETL Script - Extract and load MusicBrainz data to BigQuery.

This script processes MusicBrainz PostgreSQL dump files and loads them to BigQuery.

MusicBrainz dump format:
- Tab-separated values (TSV)
- UTF-8 encoded
- Uses \\N for NULL values
- Files are inside tar.bz2 archives

Usage:
    # Full pipeline (download, extract, transform, load)
    python scripts/musicbrainz_etl.py all

    # Individual steps
    python scripts/musicbrainz_etl.py download
    python scripts/musicbrainz_etl.py extract
    python scripts/musicbrainz_etl.py load-mapping  # Load existing MBID-Spotify mapping
    python scripts/musicbrainz_etl.py load-artists
    python scripts/musicbrainz_etl.py load-tags

    # Check status
    python scripts/musicbrainz_etl.py status

Environment:
    - Requires google-cloud-bigquery and google-cloud-storage
    - Run on VM with 50GB+ disk space for extraction
    - Or run locally with sufficient storage
"""

import json
import re
import tarfile
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

import click
from google.cloud import bigquery, storage
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

# Configuration
PROJECT_ID = "nomadkaraoke"
DATASET_ID = "karaoke_decide"
GCS_BUCKET = "nomadkaraoke-musicbrainz-data"
GCS_RAW_PREFIX = "raw/20260114-001935"
GCS_PROCESSED_PREFIX = "processed"

# Work directory
WORK_DIR = Path("/tmp/musicbrainz_etl")

console = Console()


@dataclass
class ArtistRecord:
    """MusicBrainz artist record."""

    artist_mbid: str
    name: str
    sort_name: str | None
    disambiguation: str | None
    artist_type: str | None
    begin_year: int | None
    end_year: int | None
    area_name: str | None
    gender: str | None


@dataclass
class ArtistTagRecord:
    """MusicBrainz artist tag record."""

    artist_mbid: str
    tag_name: str
    vote_count: int


# MusicBrainz PostgreSQL dump column definitions
# See: https://musicbrainz.org/doc/MusicBrainz_Database/Schema

# artist table columns (positions are 0-indexed)
# id, gid, name, sort_name, begin_date_year, begin_date_month, begin_date_day,
# end_date_year, end_date_month, end_date_day, type, area, gender,
# comment, edits_pending, last_updated, ended, begin_area, end_area
ARTIST_COLS = {
    "id": 0,
    "gid": 1,  # This is the MBID (UUID)
    "name": 2,
    "sort_name": 3,
    "begin_date_year": 4,
    "end_date_year": 7,
    "type": 10,  # Artist type ID
    "area": 11,  # Area ID
    "gender": 12,  # Gender ID
    "comment": 13,  # Disambiguation comment
}

# artist_type table columns
# id, name, parent, child_order, description, gid
ARTIST_TYPE_COLS = {"id": 0, "name": 1}

# area table columns
# id, gid, name, type, edits_pending, last_updated, begin_date_year, ...
AREA_COLS = {"id": 0, "gid": 1, "name": 2}

# gender table columns
# id, name, parent, child_order, description, gid
GENDER_COLS = {"id": 0, "name": 1}

# artist_tag table columns (from mbdump-derived)
# artist, tag, count, last_updated
ARTIST_TAG_COLS = {"artist_id": 0, "tag_id": 1, "count": 2}

# tag table columns (from mbdump-derived)
# id, name, ref_count
TAG_COLS = {"id": 0, "name": 1}


def ensure_work_dir() -> Path:
    """Ensure work directory exists."""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    return WORK_DIR


def parse_int(value: str) -> int | None:
    """Parse integer from dump, handling NULL."""
    if value == "\\N" or not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_string(value: str) -> str | None:
    """Parse string from dump, handling NULL and escapes."""
    if value == "\\N":
        return None
    # Unescape PostgreSQL escapes
    value = value.replace("\\t", "\t")
    value = value.replace("\\n", "\n")
    value = value.replace("\\\\", "\\")
    return value if value else None


def stream_tsv_from_tar(
    tar_path: Path,
    member_name: str,
) -> Generator[list[str], None, None]:
    """Stream TSV lines from a file inside a tar archive.

    Handles both plain tar and tar.bz2.
    """
    mode = "r:bz2" if str(tar_path).endswith(".bz2") else "r"

    with tarfile.open(tar_path, mode) as tar:
        # Find the member - must match exact filename (not just endswith)
        member = None
        for m in tar.getmembers():
            # Match either exact name or path ending with /name
            if m.name == member_name or m.name.endswith("/" + member_name):
                member = m
                break

        if not member:
            raise FileNotFoundError(f"Member {member_name} not found in {tar_path}")

        console.print(f"[dim]Extracting {member.name} ({member.size:,} bytes)[/dim]")

        f = tar.extractfile(member)
        if f is None:
            raise ValueError(f"Could not extract {member_name}")

        for line in f:
            decoded = line.decode("utf-8").rstrip("\n")
            yield decoded.split("\t")


def load_lookup_table(tar_path: Path, member_name: str, id_col: int, name_col: int) -> dict[str, str]:
    """Load a lookup table (type, area, gender, tag) from dump."""
    lookup = {}
    for row in stream_tsv_from_tar(tar_path, member_name):
        try:
            id_val = row[id_col]
            name_val = parse_string(row[name_col])
            if id_val and name_val:
                lookup[id_val] = name_val
        except IndexError:
            continue
    return lookup


def download_dumps(force: bool = False) -> tuple[Path, Path]:
    """Download MusicBrainz dumps from GCS."""
    work_dir = ensure_work_dir()

    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(GCS_BUCKET)

    files = [
        ("mbdump.tar.bz2", work_dir / "mbdump.tar.bz2"),
        ("mbdump-derived.tar.bz2", work_dir / "mbdump-derived.tar.bz2"),
    ]

    for gcs_name, local_path in files:
        if local_path.exists() and not force:
            console.print(f"[yellow]Already downloaded:[/yellow] {local_path}")
            continue

        gcs_path = f"{GCS_RAW_PREFIX}/{gcs_name}"
        console.print(f"[blue]Downloading:[/blue] gs://{GCS_BUCKET}/{gcs_path}")

        blob = bucket.blob(gcs_path)
        blob.download_to_filename(str(local_path))

        size_mb = local_path.stat().st_size / (1024 * 1024)
        console.print(f"[green]Downloaded:[/green] {local_path} ({size_mb:.1f} MB)")

    return work_dir / "mbdump.tar.bz2", work_dir / "mbdump-derived.tar.bz2"


def extract_artists(mbdump_path: Path) -> Generator[dict, None, None]:
    """Extract artist records from mbdump.tar.bz2."""
    console.print("[blue]Loading lookup tables...[/blue]")

    # Load lookup tables
    artist_types = load_lookup_table(mbdump_path, "artist_type", ARTIST_TYPE_COLS["id"], ARTIST_TYPE_COLS["name"])
    console.print(f"  Artist types: {len(artist_types)}")

    areas = load_lookup_table(mbdump_path, "area", AREA_COLS["id"], AREA_COLS["name"])
    console.print(f"  Areas: {len(areas)}")

    genders = load_lookup_table(mbdump_path, "gender", GENDER_COLS["id"], GENDER_COLS["name"])
    console.print(f"  Genders: {len(genders)}")

    console.print("[blue]Extracting artists...[/blue]")

    for row in stream_tsv_from_tar(mbdump_path, "artist"):
        try:
            mbid = row[ARTIST_COLS["gid"]]
            name = parse_string(row[ARTIST_COLS["name"]])

            if not mbid or not name:
                continue

            # Normalize name for search
            normalized = re.sub(r"[^a-z0-9 ]", " ", name.lower())
            normalized = re.sub(r"\s+", " ", normalized).strip()

            record = {
                "artist_mbid": mbid,
                "name": name,
                "sort_name": parse_string(row[ARTIST_COLS["sort_name"]]),
                "disambiguation": parse_string(row[ARTIST_COLS["comment"]]),
                "artist_type": artist_types.get(row[ARTIST_COLS["type"]]),
                "begin_year": parse_int(row[ARTIST_COLS["begin_date_year"]]),
                "end_year": parse_int(row[ARTIST_COLS["end_date_year"]]),
                "area_name": areas.get(row[ARTIST_COLS["area"]]),
                "gender": genders.get(row[ARTIST_COLS["gender"]]),
                "name_normalized": normalized,
            }

            yield record

        except IndexError:
            continue


def extract_artist_tags(
    mbdump_derived_path: Path,
    mbdump_path: Path,
) -> Generator[dict, None, None]:
    """Extract artist tag records from mbdump-derived.tar.bz2.

    Needs mbdump for artist ID to MBID mapping.
    """
    console.print("[blue]Building artist ID to MBID mapping...[/blue]")

    # Build artist_id -> mbid mapping
    artist_id_to_mbid: dict[str, str] = {}
    for row in stream_tsv_from_tar(mbdump_path, "artist"):
        try:
            artist_id = row[ARTIST_COLS["id"]]
            mbid = row[ARTIST_COLS["gid"]]
            if artist_id and mbid:
                artist_id_to_mbid[artist_id] = mbid
        except IndexError:
            continue

    console.print(f"  Mapped {len(artist_id_to_mbid):,} artists")

    console.print("[blue]Loading tag lookup...[/blue]")
    tags = load_lookup_table(mbdump_derived_path, "tag", TAG_COLS["id"], TAG_COLS["name"])
    console.print(f"  Tags: {len(tags)}")

    console.print("[blue]Extracting artist tags...[/blue]")

    for row in stream_tsv_from_tar(mbdump_derived_path, "artist_tag"):
        try:
            artist_id = row[ARTIST_TAG_COLS["artist_id"]]
            tag_id = row[ARTIST_TAG_COLS["tag_id"]]
            count = parse_int(row[ARTIST_TAG_COLS["count"]])

            mbid = artist_id_to_mbid.get(artist_id)
            tag_name = tags.get(tag_id)

            if not mbid or not tag_name or count is None:
                continue

            yield {
                "artist_mbid": mbid,
                "tag_name": tag_name,
                "vote_count": count,
            }

        except IndexError:
            continue


def write_ndjson(records: Generator[dict, None, None], output_path: Path, description: str) -> int:
    """Write records to NDJSON file with progress."""
    count = 0
    with open(output_path, "w", encoding="utf-8") as f:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(description, total=None)
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1
                if count % 100000 == 0:
                    progress.update(task, description=f"{description} ({count:,} records)")

    console.print(f"[green]Wrote {count:,} records to {output_path}[/green]")
    return count


def upload_to_gcs(local_path: Path, gcs_path: str) -> str:
    """Upload file to GCS."""
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(GCS_BUCKET)
    blob = bucket.blob(gcs_path)

    console.print(f"[blue]Uploading to gs://{GCS_BUCKET}/{gcs_path}...[/blue]")
    blob.upload_from_filename(str(local_path))

    return f"gs://{GCS_BUCKET}/{gcs_path}"


def load_to_bigquery(
    gcs_uri: str,
    table_id: str,
    schema: list[bigquery.SchemaField],
    write_disposition: str = "WRITE_TRUNCATE",
) -> None:
    """Load NDJSON from GCS to BigQuery."""
    client = bigquery.Client(project=PROJECT_ID)
    full_table_id = f"{PROJECT_ID}.{DATASET_ID}.{table_id}"

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        schema=schema,
        write_disposition=write_disposition,
    )

    console.print(f"[blue]Loading to {full_table_id}...[/blue]")

    load_job = client.load_table_from_uri(gcs_uri, full_table_id, job_config=job_config)
    load_job.result()  # Wait for job to complete

    table = client.get_table(full_table_id)
    console.print(f"[green]Loaded {table.num_rows:,} rows to {full_table_id}[/green]")


# BigQuery schemas
MB_ARTISTS_SCHEMA = [
    bigquery.SchemaField("artist_mbid", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("sort_name", "STRING"),
    bigquery.SchemaField("disambiguation", "STRING"),
    bigquery.SchemaField("artist_type", "STRING"),
    bigquery.SchemaField("begin_year", "INT64"),
    bigquery.SchemaField("end_year", "INT64"),
    bigquery.SchemaField("area_name", "STRING"),
    bigquery.SchemaField("gender", "STRING"),
    bigquery.SchemaField("name_normalized", "STRING"),
]

MB_ARTIST_TAGS_SCHEMA = [
    bigquery.SchemaField("artist_mbid", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("tag_name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("vote_count", "INT64"),
]

MBID_SPOTIFY_MAPPING_SCHEMA = [
    bigquery.SchemaField("artist_mbid", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("spotify_artist_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("artist_name", "STRING"),
]


# CLI Commands


@click.group()
def cli():
    """MusicBrainz ETL - Extract and load MusicBrainz data to BigQuery."""
    pass


@cli.command()
@click.option("--force", is_flag=True, help="Force re-download")
def download(force: bool):
    """Download MusicBrainz dumps from GCS."""
    download_dumps(force=force)


@cli.command()
def extract():
    """Extract data from MusicBrainz dumps to NDJSON."""
    work_dir = ensure_work_dir()
    mbdump_path = work_dir / "mbdump.tar.bz2"
    mbdump_derived_path = work_dir / "mbdump-derived.tar.bz2"

    if not mbdump_path.exists() or not mbdump_derived_path.exists():
        console.print("[red]Dumps not found. Run 'download' first.[/red]")
        return

    # Extract artists
    artists_path = work_dir / "mb_artists.ndjson"
    write_ndjson(extract_artists(mbdump_path), artists_path, "Extracting artists")

    # Extract artist tags
    tags_path = work_dir / "mb_artist_tags.ndjson"
    write_ndjson(
        extract_artist_tags(mbdump_derived_path, mbdump_path),
        tags_path,
        "Extracting artist tags",
    )


@cli.command("load-mapping")
def load_mapping():
    """Load existing MBID-Spotify mapping to BigQuery."""
    # Convert JSON to NDJSON
    console.print("[blue]Converting mapping JSON to NDJSON...[/blue]")

    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(GCS_BUCKET)
    blob = bucket.blob(f"{GCS_PROCESSED_PREFIX}/mbid_spotify_mapping.json")

    # Download and convert
    work_dir = ensure_work_dir()
    json_path = work_dir / "mbid_spotify_mapping.json"
    ndjson_path = work_dir / "mbid_spotify_mapping.ndjson"

    blob.download_to_filename(str(json_path))

    with open(json_path) as f:
        mapping = json.load(f)

    count = 0
    with open(ndjson_path, "w") as f:
        for mbid, data in mapping.items():
            record = {
                "artist_mbid": mbid,
                "spotify_artist_id": data.get("spotify_id"),
                "artist_name": data.get("name"),
            }
            f.write(json.dumps(record) + "\n")
            count += 1

    console.print(f"[green]Converted {count:,} mappings[/green]")

    # Upload and load
    gcs_uri = upload_to_gcs(ndjson_path, f"{GCS_PROCESSED_PREFIX}/mbid_spotify_mapping.ndjson")
    load_to_bigquery(gcs_uri, "mbid_spotify_mapping", MBID_SPOTIFY_MAPPING_SCHEMA)


@cli.command("load-artists")
def load_artists():
    """Load extracted artists to BigQuery."""
    work_dir = ensure_work_dir()
    artists_path = work_dir / "mb_artists.ndjson"

    if not artists_path.exists():
        console.print("[red]Artists file not found. Run 'extract' first.[/red]")
        return

    gcs_uri = upload_to_gcs(artists_path, f"{GCS_PROCESSED_PREFIX}/mb_artists.ndjson")
    load_to_bigquery(gcs_uri, "mb_artists", MB_ARTISTS_SCHEMA)


@cli.command("load-tags")
def load_tags():
    """Load extracted artist tags to BigQuery."""
    work_dir = ensure_work_dir()
    tags_path = work_dir / "mb_artist_tags.ndjson"

    if not tags_path.exists():
        console.print("[red]Tags file not found. Run 'extract' first.[/red]")
        return

    gcs_uri = upload_to_gcs(tags_path, f"{GCS_PROCESSED_PREFIX}/mb_artist_tags.ndjson")
    load_to_bigquery(gcs_uri, "mb_artist_tags", MB_ARTIST_TAGS_SCHEMA)


@cli.command()
def status():
    """Check status of MusicBrainz data in BigQuery."""
    client = bigquery.Client(project=PROJECT_ID)

    tables = [
        ("mb_artists", "Artist catalog"),
        ("mb_artist_tags", "Artist tags"),
        ("mbid_spotify_mapping", "MBID-Spotify mapping"),
    ]

    table_display = Table(title="MusicBrainz Tables in BigQuery")
    table_display.add_column("Table", style="cyan")
    table_display.add_column("Description")
    table_display.add_column("Rows", justify="right")
    table_display.add_column("Status")

    for table_id, description in tables:
        full_id = f"{PROJECT_ID}.{DATASET_ID}.{table_id}"
        try:
            table = client.get_table(full_id)
            table_display.add_row(
                table_id,
                description,
                f"{table.num_rows:,}",
                "[green]EXISTS[/green]",
            )
        except Exception:
            table_display.add_row(
                table_id,
                description,
                "-",
                "[red]MISSING[/red]",
            )

    console.print(table_display)


@cli.command()
@click.option("--skip-download", is_flag=True, help="Skip download if files exist")
def all(skip_download: bool):
    """Run full ETL pipeline."""
    console.print("[bold blue]Starting MusicBrainz ETL Pipeline[/bold blue]")
    console.print()

    # Step 1: Download (if needed)
    if not skip_download:
        console.print("[bold]Step 1: Downloading dumps...[/bold]")
        download_dumps(force=False)
    else:
        console.print("[bold]Step 1: Skipping download[/bold]")

    console.print()

    # Step 2: Load existing mapping
    console.print("[bold]Step 2: Loading MBID-Spotify mapping...[/bold]")
    ctx = click.Context(load_mapping)
    ctx.invoke(load_mapping)
    console.print()

    # Step 3: Extract data
    console.print("[bold]Step 3: Extracting data from dumps...[/bold]")
    ctx = click.Context(extract)
    ctx.invoke(extract)
    console.print()

    # Step 4: Load artists
    console.print("[bold]Step 4: Loading artists to BigQuery...[/bold]")
    ctx = click.Context(load_artists)
    ctx.invoke(load_artists)
    console.print()

    # Step 5: Load tags
    console.print("[bold]Step 5: Loading artist tags to BigQuery...[/bold]")
    ctx = click.Context(load_tags)
    ctx.invoke(load_tags)
    console.print()

    # Final status
    console.print("[bold]Final Status:[/bold]")
    ctx = click.Context(status)
    ctx.invoke(status)

    console.print()
    console.print("[bold green]ETL Pipeline Complete![/bold green]")


if __name__ == "__main__":
    cli()
