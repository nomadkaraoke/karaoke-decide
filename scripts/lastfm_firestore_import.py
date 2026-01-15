#!/usr/bin/env python3
"""
Last.fm to Firestore Import Script (MBID-First)

Imports Last.fm user data into Firestore's lastfm_users collection.
This enables collaborative filtering recommendations based on 10,000+ users.

MBID-First Architecture:
- MusicBrainz IDs (MBIDs) are the PRIMARY artist identifier
- ~80-87% of Last.fm artists have MBIDs directly from the API
- Artist names kept for backwards compatibility and display
- Spotify IDs optional (from separate mapping if available)

Data Model:
- artist_mbids: Array of MBIDs for efficient array_contains queries
- top_artists: Full artist data including mbid, name, playcount
- artist_names_lower: Legacy field for name-based queries (backwards compat)

Prerequisites:
- Artist fetching complete (scripts/lastfm_import.py)
- Optional: Spotify mapping (scripts/lastfm_spotify_mapping.py)

Usage:
    # Run full import
    python scripts/lastfm_firestore_import.py

    # Dry run (validate data, don't write)
    python scripts/lastfm_firestore_import.py --dry-run

    # Show import status
    python scripts/lastfm_firestore_import.py --status

    # Delete all imported users
    python scripts/lastfm_firestore_import.py --delete-all
"""

import argparse
import json
import re
import time

from google.cloud import firestore, storage

# Configuration
GCS_BUCKET = "nomadkaraoke-lastfm-cache"
FIRESTORE_COLLECTION = "lastfm_users"
# Batch size reduced to 20 due to large document sizes
# Each user can have up to MAX_ARTISTS_PER_USER artists
BATCH_SIZE = 20
MAX_ARTISTS_PER_USER = 500  # Limit from 1000 to reduce doc size


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

    def list_blobs(self, prefix: str) -> list[str]:
        """List blobs with prefix."""
        return [b.name for b in self.bucket.list_blobs(prefix=prefix)]


def sanitize_doc_id(username: str) -> str:
    """Sanitize username for use as Firestore document ID."""
    # Lowercase and replace invalid characters
    doc_id = username.lower()
    doc_id = re.sub(r"[/\\]", "_", doc_id)
    doc_id = re.sub(r"[^a-z0-9_-]", "", doc_id)
    # Ensure not empty and not too long
    if not doc_id:
        doc_id = "unknown"
    return doc_id[:1500]  # Firestore doc ID limit


def build_user_document(
    username: str,
    user_info: dict | None,
    artists: list[dict],
    spotify_mapping: dict | None = None,
) -> dict:
    """Build a Firestore document for a Last.fm user.

    MBID-First: MusicBrainz IDs are extracted directly from Last.fm API responses.
    Spotify IDs are optional enrichment from a separate mapping file.

    Args:
        username: Last.fm username
        user_info: User info from Last.fm API (optional)
        artists: List of artist dicts from Last.fm API (includes mbid field)
        spotify_mapping: Optional Spotify ID mapping dict

    Returns:
        Firestore document dict with MBID as primary identifier
    """
    top_artists = []
    artist_mbids = []  # Primary identifier array for queries
    artist_names_lower = []  # Backwards compatibility
    spotify_ids = []  # Optional enrichment

    mappings = spotify_mapping.get("mappings", {}) if spotify_mapping else {}

    for artist in artists[:MAX_ARTISTS_PER_USER]:  # Limit artists per user
        name = artist.get("name", "")
        if not name:
            continue

        name_lower = name.lower()
        playcount = int(artist.get("playcount", 0))

        # Extract MBID directly from Last.fm response (primary identifier)
        mbid = artist.get("mbid", "")

        # Look up Spotify mapping (optional enrichment)
        spotify_info = mappings.get(name_lower, {})
        spotify_id = spotify_info.get("spotify_id")

        top_artists.append(
            {
                "mbid": mbid,  # PRIMARY identifier
                "name": name,
                "playcount": playcount,
                "spotify_id": spotify_id,  # Optional enrichment
                "spotify_name": spotify_info.get("spotify_name"),
            }
        )

        # Build query arrays
        if mbid:
            artist_mbids.append(mbid)
        artist_names_lower.append(name_lower)
        if spotify_id:
            spotify_ids.append(spotify_id)

    # Calculate MBID coverage stats
    mbid_count = len(artist_mbids)
    total_count = len(top_artists)
    mbid_coverage = mbid_count / total_count if total_count > 0 else 0

    return {
        "lastfm_username": username,
        "lastfm_url": f"https://www.last.fm/user/{username}",
        "playcount": int(user_info.get("playcount", 0)) if user_info else 0,
        "imported_at": firestore.SERVER_TIMESTAMP,
        "source": "lastfm_friends_crawl",
        # Artist data
        "top_artists": top_artists,
        "artist_count": total_count,
        # MBID-first query arrays
        "artist_mbids": artist_mbids,  # PRIMARY for queries
        "mbid_count": mbid_count,
        "mbid_coverage": mbid_coverage,
        # Backwards compatibility arrays
        "artist_names_lower": artist_names_lower,
        "spotify_artist_ids": spotify_ids,
        "top_artist_names": [a["name"] for a in top_artists[:100]],
    }


def run_import(gcs: GCSStorage, db: firestore.Client, dry_run: bool = False):
    """Run the Firestore import (MBID-First).

    MBIDs are extracted directly from Last.fm API responses.
    Spotify mapping is optional enrichment.
    """
    print("\n🔥 Last.fm to Firestore Import (MBID-First)")
    print("=" * 60)

    # Try to load Spotify mapping (optional)
    print("\n📋 Loading Spotify artist mapping (optional)...")
    spotify_mapping = gcs.read_json("processed/artist_mapping.json")
    if spotify_mapping:
        stats = spotify_mapping.get("stats", {})
        print(f"   Loaded mapping: {stats.get('total_artists', 0):,} artists")
        print(f"   Match rate: {stats.get('match_rate', 0):.1%}")
    else:
        print("   No Spotify mapping found (MBIDs will still be imported)")

    # List all cached artist files
    print("\n📋 Loading cached user artist data...")
    artist_files = gcs.list_blobs("requests/user.getTopArtists/")
    print(f"   Found {len(artist_files)} user files")

    if not artist_files:
        print("❌ No artist data found. Run lastfm_import.py first.")
        return

    # Process users
    print(f"\n{'🔍 DRY RUN - ' if dry_run else ''}Importing users to Firestore...")

    batch = db.batch() if not dry_run else None
    batch_count = 0
    total_imported = 0
    total_artists = 0
    total_with_mbid = 0  # MBID stats (primary)
    total_with_spotify = 0  # Spotify stats (optional)
    errors = 0

    start_time = time.time()

    for i, file_path in enumerate(artist_files):
        # Extract username from path
        # Format: requests/user.getTopArtists/username.json
        username = file_path.split("/")[-1].replace(".json", "")

        # Load artist data
        data = gcs.read_json(file_path)
        if not data or "response" not in data:
            errors += 1
            continue

        response = data["response"]
        if "error" in response:
            errors += 1
            continue

        # Extract artists
        artists_data = response.get("topartists", {})
        artists = artists_data.get("artist", [])
        if isinstance(artists, dict):
            artists = [artists]

        if not artists:
            errors += 1
            continue

        # Load user info if available
        user_info_data = gcs.read_json(f"requests/user.getInfo/{username}.json")
        user_info = None
        if user_info_data and "response" in user_info_data:
            user_info = user_info_data["response"].get("user", {})

        # Build document (MBID-first)
        doc = build_user_document(username, user_info, artists, spotify_mapping)

        # Track stats
        total_artists += doc["artist_count"]
        total_with_mbid += doc["mbid_count"]  # Primary metric
        total_with_spotify += len(doc["spotify_artist_ids"])  # Secondary

        if not dry_run:
            # Add to batch
            doc_id = sanitize_doc_id(username)
            ref = db.collection(FIRESTORE_COLLECTION).document(doc_id)
            batch.set(ref, doc)
            batch_count += 1

            # Commit batch if full
            if batch_count >= BATCH_SIZE:
                batch.commit()
                batch = db.batch()
                batch_count = 0

        total_imported += 1

        # Progress update
        if (i + 1) % 500 == 0:
            elapsed = time.time() - start_time
            rate = total_imported / elapsed if elapsed > 0 else 0
            remaining = len(artist_files) - i - 1
            eta = remaining / rate if rate > 0 else 0
            mbid_rate = total_with_mbid / total_artists if total_artists > 0 else 0
            print(
                f"   [{i + 1}/{len(artist_files)}] {total_imported} users, MBID coverage: {mbid_rate:.1%}, ETA: {eta / 60:.1f}min"
            )

    # Commit remaining batch
    if not dry_run and batch_count > 0:
        batch.commit()

    # Print summary
    elapsed = time.time() - start_time
    avg_artists = total_artists / total_imported if total_imported > 0 else 0
    mbid_rate = total_with_mbid / total_artists if total_artists > 0 else 0
    spotify_rate = total_with_spotify / total_artists if total_artists > 0 else 0

    print("\n" + "=" * 60)
    print("IMPORT SUMMARY (MBID-First)")
    print("=" * 60)
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"Users imported: {total_imported:,}")
    print(f"Errors/skipped: {errors}")
    print(f"Total artists: {total_artists:,}")
    print(f"Avg artists/user: {avg_artists:.0f}")
    print("-" * 40)
    print("PRIMARY: MusicBrainz IDs (from Last.fm API)")
    print(f"  Artists with MBID: {total_with_mbid:,} ({mbid_rate:.1%})")
    print("-" * 40)
    print("OPTIONAL: Spotify IDs (from mapping file)")
    print(f"  Artists with Spotify ID: {total_with_spotify:,} ({spotify_rate:.1%})")
    print("-" * 40)
    print(f"Time: {elapsed:.1f}s")
    print("=" * 60)

    if dry_run:
        print("\n✓ Dry run complete. Run without --dry-run to import.")
    else:
        print(f"\n✓ Imported {total_imported:,} users to Firestore (MBID-first).")


def show_status(db: firestore.Client):
    """Show current import status (MBID-First)."""
    print("\n" + "=" * 60)
    print("FIRESTORE IMPORT STATUS (MBID-First)")
    print("=" * 60)

    # Count documents
    try:
        # Use aggregation query for count
        collection_ref = db.collection(FIRESTORE_COLLECTION)
        count_query = collection_ref.count()
        count_result = count_query.get()
        total_count = count_result[0][0].value

        print(f"\nCollection: {FIRESTORE_COLLECTION}")
        print(f"Document count: {total_count:,}")

        # Sample some documents
        if total_count > 0:
            print("\nSample documents:")
            docs = collection_ref.limit(5).stream()
            for doc in docs:
                data = doc.to_dict()
                mbid_count = data.get("mbid_count", len(data.get("artist_mbids", [])))
                artist_count = data.get("artist_count", 0)
                mbid_coverage = mbid_count / artist_count if artist_count > 0 else 0
                print(f"  {doc.id}:")
                print(f"    Artists: {artist_count}")
                print(f"    With MBID: {mbid_count} ({mbid_coverage:.1%})")
                print(f"    With Spotify: {len(data.get('spotify_artist_ids', []))}")
                # Show sample MBIDs
                mbids = data.get("artist_mbids", [])[:2]
                if mbids:
                    print(f"    Sample MBIDs: {', '.join(mbids[:2])}")

            # MBID query test (primary)
            # Test with Radiohead's MBID: a74b1b7f-71a5-4011-9441-d0b5e4122711
            radiohead_mbid = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
            print(f"\nMBID Query test (Radiohead: {radiohead_mbid[:8]}...):")
            mbid_results = collection_ref.where("artist_mbids", "array_contains", radiohead_mbid).limit(10).stream()
            mbid_count = sum(1 for _ in mbid_results)
            print(f"  Found {mbid_count} users via MBID query")

            # Name query test (backwards compat)
            print("\nName Query test (users who like 'radiohead'):")
            name_results = collection_ref.where("artist_names_lower", "array_contains", "radiohead").limit(10).stream()
            name_count = sum(1 for _ in name_results)
            print(f"  Found {name_count} users via name query")

    except Exception as e:
        print(f"Error: {e}")

    print("=" * 60)


def delete_all(db: firestore.Client):
    """Delete all documents in the lastfm_users collection."""
    print("\n⚠️  Deleting all documents from lastfm_users collection...")

    confirm = input("Are you sure? Type 'DELETE' to confirm: ")
    if confirm != "DELETE":
        print("Cancelled.")
        return

    collection_ref = db.collection(FIRESTORE_COLLECTION)
    batch = db.batch()
    count = 0
    total_deleted = 0

    docs = collection_ref.stream()
    for doc in docs:
        batch.delete(doc.reference)
        count += 1
        total_deleted += 1

        if count >= BATCH_SIZE:
            batch.commit()
            batch = db.batch()
            count = 0
            print(f"  Deleted {total_deleted}...")

    if count > 0:
        batch.commit()

    print(f"\n✓ Deleted {total_deleted} documents.")


def main():
    parser = argparse.ArgumentParser(description="Last.fm to Firestore Import")
    parser.add_argument("--dry-run", action="store_true", help="Validate without writing")
    parser.add_argument("--status", action="store_true", help="Show import status")
    parser.add_argument("--delete-all", action="store_true", help="Delete all imported users")

    args = parser.parse_args()

    print("Initializing clients...")
    gcs = GCSStorage(GCS_BUCKET)
    db = firestore.Client()

    if args.status:
        show_status(db)
    elif args.delete_all:
        delete_all(db)
    else:
        run_import(gcs, db, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
