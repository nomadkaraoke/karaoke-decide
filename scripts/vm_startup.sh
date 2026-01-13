#!/bin/bash
# MLHD+ Download and Processing VM Startup Script
# This script runs on a GCE VM to download and process the full MLHD+ dataset
#
# VM recommended specs: e2-standard-8 (8 vCPU, 32GB RAM), 500GB SSD
# Estimated time: 4-8 hours for download, 8-16 hours for processing
#
# Usage:
#   gcloud compute instances create mlhd-import \
#     --project=nomadkaraoke \
#     --zone=us-central1-a \
#     --machine-type=e2-standard-8 \
#     --boot-disk-size=500GB \
#     --boot-disk-type=pd-ssd \
#     --image-family=debian-12 \
#     --image-project=debian-cloud \
#     --scopes=storage-rw \
#     --metadata-from-file=startup-script=scripts/vm_startup.sh

set -e

# Configuration
GCS_BUCKET="gs://nomadkaraoke-mlhd-data"
DATA_DIR="/data/mlhd"
LOG_FILE="/var/log/mlhd-import.log"

# Log everything
exec > >(tee -a "$LOG_FILE") 2>&1
echo "=== MLHD+ Import Started at $(date) ==="
echo "GCS Bucket: $GCS_BUCKET"

# Install dependencies
apt-get update
apt-get install -y python3 python3-venv python3-pip pv

# Create working directory
mkdir -p "$DATA_DIR"
cd /data

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python packages
pip install httpx rich click zstandard

# Create the import script
cat > /data/mlhd_import.py << 'PYTHON_SCRIPT'
#!/usr/bin/env python3
"""
MLHD+ Import Script for VM

Phases:
1. Download all archives and backup to GCS immediately
2. Process each archive to extract user listening histories
3. Build artist co-occurrence matrix
4. Upload final results to GCS
"""

import json
import os
import subprocess
import tarfile
import tempfile
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from datetime import datetime

import httpx
import zstandard as zstd

MLHD_BASE_URL = "https://data.musicbrainz.org/pub/musicbrainz/listenbrainz/mlhd"
ARCHIVES = [f"mlhdplus-complete-{hex(i)[2:]}.tar" for i in range(16)]
GCS_BUCKET = os.environ.get("GCS_BUCKET", "gs://nomadkaraoke-mlhd-data")


def log(msg: str):
    """Print timestamped log message."""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def upload_to_gcs(local_path: Path, gcs_path: str):
    """Upload file to GCS."""
    log(f"Uploading {local_path.name} to {gcs_path}...")
    subprocess.run(
        ["gsutil", "-m", "cp", str(local_path), gcs_path],
        check=True
    )
    log(f"Uploaded {local_path.name}")


def download_and_backup(output_dir: Path) -> list[Path]:
    """Download all archives and backup to GCS immediately."""
    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded = []

    for archive_name in ARCHIVES:
        url = f"{MLHD_BASE_URL}/{archive_name}"
        output_path = output_dir / archive_name
        gcs_raw_path = f"{GCS_BUCKET}/raw/{archive_name}"

        # Check if already in GCS
        result = subprocess.run(
            ["gsutil", "stat", gcs_raw_path],
            capture_output=True
        )
        if result.returncode == 0:
            log(f"Skipping {archive_name} (already in GCS)")
            # Download from GCS if not local
            if not output_path.exists():
                log(f"Downloading {archive_name} from GCS...")
                subprocess.run(
                    ["gsutil", "cp", gcs_raw_path, str(output_path)],
                    check=True
                )
            downloaded.append(output_path)
            continue

        if output_path.exists():
            log(f"Found local {archive_name}, uploading to GCS...")
            upload_to_gcs(output_path, gcs_raw_path)
            downloaded.append(output_path)
            continue

        log(f"Downloading {archive_name} from source...")
        with httpx.Client(timeout=None, follow_redirects=True) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length", 0))
                downloaded_bytes = 0

                with open(output_path, "wb") as f:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
                        downloaded_bytes += len(chunk)
                        if total:
                            pct = 100 * downloaded_bytes / total
                            print(
                                f"\r  {downloaded_bytes / (1024**3):.2f}GB / "
                                f"{total / (1024**3):.2f}GB ({pct:.1f}%)",
                                end="",
                                flush=True
                            )
        print()  # newline after progress
        log(f"Downloaded {archive_name}")

        # Immediately backup to GCS
        upload_to_gcs(output_path, gcs_raw_path)
        downloaded.append(output_path)

    return downloaded


def extract_user_artists(filepath: Path) -> dict | None:
    """Extract artists from a user file."""
    user_id = filepath.stem.replace(".txt", "")
    artist_counts = defaultdict(int)

    try:
        dctx = zstd.ZstdDecompressor()
        with open(filepath, "rb") as fh:
            with dctx.stream_reader(fh) as reader:
                text = reader.read().decode("utf-8")
                for line in text.strip().split("\n"):
                    if not line:
                        continue
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        for mbid in parts[1].strip().split(","):
                            mbid = mbid.strip()
                            if mbid:
                                artist_counts[mbid] += 1
    except Exception as e:
        print(f"Error: {filepath}: {e}")
        return None

    if not artist_counts:
        return None

    return {"user_id": user_id, "artists": dict(artist_counts)}


def process_archive(archive_path: Path, output_dir: Path, min_artists: int = 10) -> Path:
    """Process a single archive and save user histories."""
    archive_name = archive_path.stem
    output_path = output_dir / f"{archive_name}_histories.json"
    gcs_path = f"{GCS_BUCKET}/processed/{archive_name}_histories.json"

    # Check if already processed
    result = subprocess.run(["gsutil", "stat", gcs_path], capture_output=True)
    if result.returncode == 0:
        log(f"Skipping {archive_name} (already processed in GCS)")
        if not output_path.exists():
            subprocess.run(["gsutil", "cp", gcs_path, str(output_path)], check=True)
        return output_path

    histories = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        log(f"Extracting {archive_path.name}...")

        with tarfile.open(archive_path, "r") as tar:
            tar.extractall(tmppath, filter="data")

        zst_files = list(tmppath.rglob("*.txt.zst"))
        log(f"Found {len(zst_files)} user files in {archive_name}")

        with ProcessPoolExecutor(max_workers=8) as executor:
            for i, result in enumerate(executor.map(extract_user_artists, zst_files)):
                if result and len(result["artists"]) >= min_artists:
                    histories.append(result)
                if (i + 1) % 10000 == 0:
                    log(f"  Processed {i+1}/{len(zst_files)} users ({len(histories)} valid)")

    log(f"Extracted {len(histories)} valid users from {archive_name}")

    # Save locally
    with open(output_path, "w") as f:
        json.dump(histories, f)

    # Upload to GCS
    upload_to_gcs(output_path, gcs_path)

    return output_path


def build_cooccurrence(history_files: list[Path], min_shared: int = 50) -> dict:
    """Build co-occurrence matrix from all history files."""
    log("Loading all user histories...")

    all_histories = []
    for hf in history_files:
        with open(hf) as f:
            histories = json.load(f)
            all_histories.extend(histories)
            log(f"  Loaded {len(histories)} from {hf.name}")

    log(f"Total users: {len(all_histories)}")
    log("Building co-occurrence matrix (this may take a while)...")

    cooccurrence = defaultdict(int)
    artist_counts = defaultdict(int)

    for i, h in enumerate(all_histories):
        artists = list(h["artists"].keys())

        # Limit artists per user to avoid explosion (top 100 by play count)
        if len(artists) > 100:
            sorted_artists = sorted(
                h["artists"].items(),
                key=lambda x: x[1],
                reverse=True
            )[:100]
            artists = [a for a, _ in sorted_artists]

        for a in artists:
            artist_counts[a] += 1

        artists_sorted = sorted(artists)
        for j, a in enumerate(artists_sorted):
            for b in artists_sorted[j + 1:]:
                cooccurrence[(a, b)] += 1

        if (i + 1) % 50000 == 0:
            log(f"  Processed {i+1}/{len(all_histories)} users, {len(cooccurrence)} pairs")

    log(f"Raw pairs: {len(cooccurrence)}")
    log(f"Filtering to pairs with {min_shared}+ shared users...")

    result = {}
    for (a, b), shared in cooccurrence.items():
        if shared >= min_shared:
            users_a = artist_counts[a]
            users_b = artist_counts[b]
            jaccard = shared / (users_a + users_b - shared)
            result[f"{a}|{b}"] = {
                "shared": shared,
                "users_a": users_a,
                "users_b": users_b,
                "jaccard": round(jaccard, 6),
            }

    log(f"Final pairs after filtering: {len(result)}")
    return result


def main():
    data_dir = Path("/data/mlhd")
    processed_dir = Path("/data/processed")
    processed_dir.mkdir(exist_ok=True)

    # Phase 1: Download and backup
    log("=" * 60)
    log("PHASE 1: DOWNLOAD AND BACKUP TO GCS")
    log("=" * 60)
    archives = download_and_backup(data_dir)
    log(f"Downloaded {len(archives)} archives")

    # Phase 2: Process each archive
    log("=" * 60)
    log("PHASE 2: EXTRACT USER HISTORIES")
    log("=" * 60)
    history_files = []
    for archive in sorted(archives):
        hf = process_archive(archive, processed_dir)
        history_files.append(hf)

    # Phase 3: Build co-occurrence
    log("=" * 60)
    log("PHASE 3: BUILD CO-OCCURRENCE MATRIX")
    log("=" * 60)
    cooccurrence = build_cooccurrence(history_files)

    # Save final results
    output_path = Path("/data/cooccurrence.json")
    with open(output_path, "w") as f:
        json.dump(cooccurrence, f)
    log(f"Saved co-occurrence to {output_path}")

    upload_to_gcs(output_path, f"{GCS_BUCKET}/cooccurrence.json")

    # Also save artist counts for reference
    log("Calculating artist statistics...")
    artist_user_counts = defaultdict(int)
    for hf in history_files:
        with open(hf) as f:
            for h in json.load(f):
                for a in h["artists"]:
                    artist_user_counts[a] += 1

    stats_path = Path("/data/artist_stats.json")
    with open(stats_path, "w") as f:
        json.dump(dict(artist_user_counts), f)
    upload_to_gcs(stats_path, f"{GCS_BUCKET}/artist_stats.json")

    # Summary
    log("=" * 60)
    log("COMPLETE!")
    log("=" * 60)
    log(f"Archives processed: {len(archives)}")
    log(f"Total users: {sum(len(json.load(open(hf))) for hf in history_files)}")
    log(f"Unique artists: {len(artist_user_counts)}")
    log(f"Artist pairs (co-occurrence): {len(cooccurrence)}")
    log(f"Results in: {GCS_BUCKET}/")


if __name__ == "__main__":
    main()
PYTHON_SCRIPT

# Set environment variable for GCS bucket
export GCS_BUCKET="$GCS_BUCKET"

# Run the import
echo "=== Starting MLHD+ Import ==="
python /data/mlhd_import.py

echo "=== MLHD+ Import Complete at $(date) ==="

# Optional: shut down VM when done (uncomment to enable)
# echo "Shutting down VM..."
# shutdown -h now
