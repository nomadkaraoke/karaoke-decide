#!/usr/bin/env python3
"""
Last.fm to Firestore Import Script

Imports processed Last.fm user data into Firestore's lastfm_users collection.
This enables collaborative filtering recommendations based on 10,000+ users.

Prerequisites:
- Artist fetching complete (scripts/lastfm_import.py)
- Spotify mapping complete (scripts/lastfm_spotify_mapping.py)

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
BATCH_SIZE = 500  # Firestore batch limit


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
    user_info: dict,
    artists: list[dict],
    mapping: dict,
) -> dict:
    """Build a Firestore document for a Last.fm user."""
    top_artists = []
    spotify_ids = []
    artist_names_lower = []

    mappings = mapping.get("mappings", {})

    for artist in artists[:1000]:  # Limit to top 1000
        name = artist.get("name", "")
        if not name:
            continue

        name_lower = name.lower()
        playcount = int(artist.get("playcount", 0))

        # Look up Spotify mapping
        spotify_info = mappings.get(name_lower, {})
        spotify_id = spotify_info.get("spotify_id")

        top_artists.append(
            {
                "name": name,
                "playcount": playcount,
                "spotify_id": spotify_id,
                "spotify_name": spotify_info.get("spotify_name"),
            }
        )

        artist_names_lower.append(name_lower)
        if spotify_id:
            spotify_ids.append(spotify_id)

    return {
        "lastfm_username": username,
        "lastfm_url": f"https://www.last.fm/user/{username}",
        "playcount": int(user_info.get("playcount", 0)) if user_info else 0,
        "imported_at": firestore.SERVER_TIMESTAMP,
        "source": "lastfm_friends_crawl",
        "top_artists": top_artists,
        "artist_count": len(top_artists),
        "artist_names_lower": artist_names_lower,
        "spotify_artist_ids": spotify_ids,
        "top_artist_names": [a["name"] for a in top_artists[:100]],
    }


def run_import(gcs: GCSStorage, db: firestore.Client, dry_run: bool = False):
    """Run the Firestore import."""
    print("\n🔥 Last.fm to Firestore Import")
    print("=" * 60)

    # Load Spotify mapping
    print("\n📋 Loading Spotify artist mapping...")
    mapping = gcs.read_json("processed/artist_mapping.json")
    if not mapping:
        print("❌ No mapping found. Run lastfm_spotify_mapping.py first.")
        return

    stats = mapping.get("stats", {})
    print(f"   Loaded mapping: {stats.get('total_artists', 0):,} artists")
    print(f"   Match rate: {stats.get('match_rate', 0):.1%}")

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
    total_mapped = 0
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

        # Build document
        doc = build_user_document(username, user_info, artists, mapping)

        # Track stats
        total_artists += doc["artist_count"]
        total_mapped += len(doc["spotify_artist_ids"])

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
            print(f"   [{i + 1}/{len(artist_files)}] {total_imported} imported, ETA: {eta / 60:.1f}min")

    # Commit remaining batch
    if not dry_run and batch_count > 0:
        batch.commit()

    # Print summary
    elapsed = time.time() - start_time
    avg_artists = total_artists / total_imported if total_imported > 0 else 0
    avg_mapped = total_mapped / total_imported if total_imported > 0 else 0
    map_rate = total_mapped / total_artists if total_artists > 0 else 0

    print("\n" + "=" * 60)
    print("IMPORT SUMMARY")
    print("=" * 60)
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"Users imported: {total_imported:,}")
    print(f"Errors/skipped: {errors}")
    print(f"Total artists: {total_artists:,}")
    print(f"Avg artists/user: {avg_artists:.0f}")
    print(f"Total mapped to Spotify: {total_mapped:,}")
    print(f"Avg mapped/user: {avg_mapped:.0f}")
    print(f"Overall map rate: {map_rate:.1%}")
    print(f"Time: {elapsed:.1f}s")
    print("=" * 60)

    if dry_run:
        print("\n✓ Dry run complete. Run without --dry-run to import.")
    else:
        print(f"\n✓ Imported {total_imported:,} users to Firestore.")


def show_status(db: firestore.Client):
    """Show current import status."""
    print("\n" + "=" * 60)
    print("FIRESTORE IMPORT STATUS")
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
                print(f"  {doc.id}:")
                print(f"    Artists: {data.get('artist_count', 0)}")
                print(f"    Spotify mapped: {len(data.get('spotify_artist_ids', []))}")
                print(f"    Top artists: {', '.join(data.get('top_artist_names', [])[:3])}")

            # Query test
            print("\nQuery test (users who like 'radiohead'):")
            test_results = collection_ref.where("artist_names_lower", "array_contains", "radiohead").limit(5).stream()
            test_count = 0
            for doc in test_results:
                test_count += 1
            print(f"  Found {test_count} users")

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
