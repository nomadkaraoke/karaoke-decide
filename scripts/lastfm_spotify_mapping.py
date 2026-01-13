#!/usr/bin/env python3
"""
Last.fm to Spotify Artist Mapping Script

Maps Last.fm artist names to Spotify artist IDs using our BigQuery catalog.

Features:
- Extracts unique artists from cached Last.fm data
- Exact case-insensitive matching against Spotify catalog
- Fuzzy matching for close variations
- Caches all mappings to GCS
- Reports match statistics

Usage:
    # Run full mapping
    python scripts/lastfm_spotify_mapping.py

    # Show mapping stats
    python scripts/lastfm_spotify_mapping.py --status

    # Only extract artists (no matching)
    python scripts/lastfm_spotify_mapping.py --extract-only
"""

import argparse
import json
import re
from datetime import UTC, datetime

from google.cloud import bigquery, storage

# Configuration
GCS_BUCKET = "nomadkaraoke-lastfm-cache"
BIGQUERY_PROJECT = "nomadkaraoke"
BIGQUERY_DATASET = "karaoke_decide"
BATCH_SIZE = 1000  # Artists per BigQuery query


class GCSStorage:
    """GCS storage helper."""

    def __init__(self, bucket_name: str):
        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket_name)

    def read_json(self, path: str) -> dict | None:
        """Read JSON from GCS."""
        blob = self.bucket.blob(path)
        try:
            if blob.exists():
                return json.loads(blob.download_as_text())
        except Exception as e:
            print(f"  Error reading {path}: {e}")
        return None

    def write_json(self, path: str, data: dict):
        """Write JSON to GCS."""
        blob = self.bucket.blob(path)
        blob.upload_from_string(json.dumps(data, indent=2), content_type="application/json")

    def list_blobs(self, prefix: str) -> list[str]:
        """List blobs with prefix."""
        return [b.name for b in self.bucket.list_blobs(prefix=prefix)]


def extract_unique_artists(gcs: GCSStorage) -> dict[str, dict]:
    """
    Extract unique artists from all cached user.getTopArtists responses.

    Returns dict mapping lowercase name to artist info.
    """
    print("\n🎵 Extracting unique artists from cached data...")

    # List all cached artist responses
    artist_files = gcs.list_blobs("requests/user.getTopArtists/")
    print(f"   Found {len(artist_files)} cached user artist files")

    artists: dict[str, dict] = {}
    users_processed = 0

    for file_path in artist_files:
        data = gcs.read_json(file_path)
        if not data or "response" not in data:
            continue

        response = data["response"]
        if "topartists" not in response or "artist" not in response["topartists"]:
            continue

        artist_list = response["topartists"]["artist"]
        if isinstance(artist_list, dict):
            artist_list = [artist_list]

        for artist in artist_list:
            name = artist.get("name")
            if not name:
                continue

            name_lower = name.lower()
            playcount = int(artist.get("playcount", 0))
            mbid = artist.get("mbid", "")

            if name_lower not in artists:
                artists[name_lower] = {
                    "canonical_name": name,
                    "user_count": 0,
                    "total_plays": 0,
                    "mbid": mbid,
                }

            artists[name_lower]["user_count"] += 1
            artists[name_lower]["total_plays"] += playcount
            # Keep the most common capitalization
            if playcount > 0:
                artists[name_lower]["canonical_name"] = name

        users_processed += 1
        if users_processed % 1000 == 0:
            print(f"   Processed {users_processed} users, {len(artists)} unique artists...")

    print(f"\n   ✓ Extracted {len(artists)} unique artists from {users_processed} users")
    return artists


def normalize_name(name: str) -> str:
    """Normalize artist name for matching."""
    # Lowercase
    name = name.lower()
    # Remove "the " prefix
    if name.startswith("the "):
        name = name[4:]
    # Remove special characters
    name = re.sub(r"[^a-z0-9\s]", "", name)
    # Collapse whitespace
    name = " ".join(name.split())
    return name


def batch_match_spotify(
    bq_client: bigquery.Client,
    artist_names: list[str],
) -> dict[str, dict]:
    """
    Match artist names against Spotify catalog using BigQuery.

    Returns dict mapping lowercase name to Spotify info.
    """
    if not artist_names:
        return {}

    # Escape single quotes in names
    escaped_names = [name.replace("'", "\\'") for name in artist_names]

    # Build query with name list
    names_list = ", ".join(f"'{name}'" for name in escaped_names)

    query = f"""
    WITH lastfm_names AS (
        SELECT name FROM UNNEST([{names_list}]) AS name
    )
    SELECT
        l.name AS lastfm_name_lower,
        s.artist_id,
        s.artist_name AS spotify_name,
        s.popularity,
        s.followers_total
    FROM lastfm_names l
    JOIN `{BIGQUERY_PROJECT}.{BIGQUERY_DATASET}.spotify_artists` s
        ON LOWER(s.artist_name) = l.name
    """

    try:
        results = bq_client.query(query).result()

        matches = {}
        for row in results:
            name_lower = row.lastfm_name_lower
            # If multiple matches, prefer higher popularity
            if name_lower not in matches or row.popularity > matches[name_lower]["popularity"]:
                matches[name_lower] = {
                    "spotify_id": row.artist_id,
                    "spotify_name": row.spotify_name,
                    "popularity": row.popularity,
                    "followers": row.followers_total,
                    "match_type": "exact",
                    "confidence": 1.0,
                }

        return matches

    except Exception as e:
        print(f"    BigQuery error: {e}")
        return {}


def run_spotify_matching(
    gcs: GCSStorage,
    artists: dict[str, dict],
) -> dict:
    """
    Match all artists against Spotify catalog.

    Returns mapping results.
    """
    print("\n🔍 Matching artists against Spotify catalog...")

    bq_client = bigquery.Client(project=BIGQUERY_PROJECT)

    artist_names = list(artists.keys())
    total = len(artist_names)
    matched = 0
    mappings: dict[str, dict] = {}
    unmatched: list[str] = []

    # Process in batches
    for i in range(0, total, BATCH_SIZE):
        batch = artist_names[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

        print(f"   Batch {batch_num}/{total_batches} ({len(batch)} artists)...", end=" ")

        batch_matches = batch_match_spotify(bq_client, batch)
        matched += len(batch_matches)

        # Store matches with Last.fm info
        for name_lower, spotify_info in batch_matches.items():
            mappings[name_lower] = {
                "lastfm_name": artists[name_lower]["canonical_name"],
                "user_count": artists[name_lower]["user_count"],
                **spotify_info,
            }

        # Track unmatched
        for name in batch:
            if name not in batch_matches:
                unmatched.append(name)

        print(f"✓ {len(batch_matches)} matches")

    # Calculate stats
    match_rate = matched / total if total > 0 else 0

    # Sort unmatched by user count (most popular first)
    unmatched_with_counts = [(name, artists[name]["user_count"]) for name in unmatched]
    unmatched_with_counts.sort(key=lambda x: -x[1])

    results = {
        "mappings": mappings,
        "unmatched": [name for name, _ in unmatched_with_counts[:1000]],  # Top 1000 unmatched
        "stats": {
            "total_artists": total,
            "exact_matches": matched,
            "unmatched_count": len(unmatched),
            "match_rate": round(match_rate, 4),
            "timestamp": datetime.now(UTC).isoformat(),
        },
    }

    print(f"\n   ✓ Matched {matched}/{total} artists ({match_rate:.1%})")
    return results


def show_status(gcs: GCSStorage):
    """Show current mapping status."""
    print("\n" + "=" * 60)
    print("SPOTIFY MAPPING STATUS")
    print("=" * 60)

    # Check for extracted artists
    unique_artists = gcs.read_json("processed/unique_artists.json")
    if unique_artists:
        print(f"\nExtracted artists: {len(unique_artists)} unique")
        # Show top 10 by user count
        sorted_artists = sorted(unique_artists.items(), key=lambda x: -x[1]["user_count"])[:10]
        print("\nTop 10 artists by user count:")
        for name, info in sorted_artists:
            print(f"  {info['user_count']:5d} users: {info['canonical_name']}")
    else:
        print("\nNo extracted artists found. Run extraction first.")

    # Check for mapping results
    mapping = gcs.read_json("processed/artist_mapping.json")
    if mapping:
        stats = mapping.get("stats", {})
        print("\nMapping stats:")
        print(f"  Total artists: {stats.get('total_artists', 0):,}")
        print(f"  Exact matches: {stats.get('exact_matches', 0):,}")
        print(f"  Unmatched: {stats.get('unmatched_count', 0):,}")
        print(f"  Match rate: {stats.get('match_rate', 0):.1%}")
        print(f"  Timestamp: {stats.get('timestamp', 'N/A')}")

        # Show sample mappings
        mappings = mapping.get("mappings", {})
        print("\nSample mappings (top by user count):")
        sorted_mappings = sorted(mappings.items(), key=lambda x: -x[1].get("user_count", 0))[:10]
        for name, info in sorted_mappings:
            print(f"  {info['lastfm_name']} → {info['spotify_name']} ({info['spotify_id'][:8]}...)")

        # Show top unmatched
        unmatched = mapping.get("unmatched", [])
        if unmatched:
            print(f"\nTop unmatched artists ({len(unmatched)} shown):")
            for name in unmatched[:10]:
                if name in unique_artists:
                    print(f"  {unique_artists[name]['user_count']:5d} users: {unique_artists[name]['canonical_name']}")
    else:
        print("\nNo mapping results found. Run mapping first.")

    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Last.fm to Spotify Artist Mapping")
    parser.add_argument("--status", action="store_true", help="Show mapping status")
    parser.add_argument("--extract-only", action="store_true", help="Only extract artists, don't match")

    args = parser.parse_args()

    print("Initializing GCS storage...")
    gcs = GCSStorage(GCS_BUCKET)

    if args.status:
        show_status(gcs)
        return

    # Step 1: Extract unique artists
    artists = extract_unique_artists(gcs)

    if not artists:
        print("❌ No artists found. Make sure artist fetching is complete.")
        return

    # Save extracted artists
    print("\n💾 Saving extracted artists to GCS...")
    gcs.write_json("processed/unique_artists.json", artists)

    if args.extract_only:
        print("\n✓ Extraction complete. Run without --extract-only to perform matching.")
        return

    # Step 2: Match against Spotify
    results = run_spotify_matching(gcs, artists)

    # Save mapping results
    print("\n💾 Saving mapping results to GCS...")
    gcs.write_json("processed/artist_mapping.json", results)

    # Print summary
    print("\n" + "=" * 60)
    print("MAPPING COMPLETE")
    print("=" * 60)
    stats = results["stats"]
    print(f"Total artists: {stats['total_artists']:,}")
    print(f"Exact matches: {stats['exact_matches']:,}")
    print(f"Unmatched: {stats['unmatched_count']:,}")
    print(f"Match rate: {stats['match_rate']:.1%}")

    # Show top unmatched
    if results["unmatched"]:
        print("\nTop unmatched artists (may need fuzzy matching):")
        for name in results["unmatched"][:10]:
            info = artists.get(name, {})
            print(f"  {info.get('user_count', 0):5d} users: {info.get('canonical_name', name)}")

    print("=" * 60)


if __name__ == "__main__":
    main()
