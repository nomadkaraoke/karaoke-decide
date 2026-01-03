#!/usr/bin/env python3
"""Migrate users from 'users' collection to 'decide_users' collection.

This script copies karaoke-decide user documents from the shared 'users' collection
to the new 'decide_users' collection. Only documents with a 'user_id' field are
migrated (karaoke-gen users don't have this field).

This separates karaoke-decide data from karaoke-gen data in the shared Firestore
database, matching the pattern karaoke-gen used with 'gen_users'.

Prerequisites:
- gcloud CLI authenticated with access to nomadkaraoke project
- python packages: google-cloud-firestore

Usage:
    # Dry run (preview what would be migrated)
    python scripts/migrate_users_collection.py --dry-run

    # Run migration
    python scripts/migrate_users_collection.py

    # Verbose mode
    python scripts/migrate_users_collection.py --verbose
"""

import argparse
import sys

try:
    from google.cloud import firestore
except ImportError:
    print("Error: Required packages not installed.", file=sys.stderr)
    print("Run: pip install google-cloud-firestore", file=sys.stderr)
    sys.exit(1)

# Configuration
PROJECT_ID = "nomadkaraoke"
SOURCE_COLLECTION = "users"
TARGET_COLLECTION = "decide_users"


def migrate_users(dry_run: bool = False, verbose: bool = False) -> tuple[int, int]:
    """Migrate karaoke-decide users to the new collection.

    Returns:
        Tuple of (migrated_count, skipped_count)
    """
    db = firestore.Client(project=PROJECT_ID)

    # Query all documents with user_id field (karaoke-decide users only)
    # karaoke-gen users don't have this field
    source_ref = db.collection(SOURCE_COLLECTION)
    target_ref = db.collection(TARGET_COLLECTION)

    migrated = 0
    skipped = 0
    errors = 0

    print(f"Querying {SOURCE_COLLECTION} collection...")
    docs = source_ref.stream()

    for doc in docs:
        data = doc.to_dict()
        doc_id = doc.id

        # Only migrate documents with user_id field (karaoke-decide users)
        if "user_id" not in data:
            if verbose:
                print(f"  Skipping {doc_id}: no user_id field (karaoke-gen user)")
            skipped += 1
            continue

        user_id = data.get("user_id", "")
        email = data.get("email", "(guest)")

        if dry_run:
            print(f"  [DRY RUN] Would migrate: {doc_id} (user_id={user_id}, email={email})")
            migrated += 1
        else:
            try:
                # Check if document already exists in target
                existing = target_ref.document(doc_id).get()
                if existing.exists:
                    if verbose:
                        print(f"  Already exists: {doc_id}, updating...")

                # Copy document to target collection with same ID
                target_ref.document(doc_id).set(data)
                if verbose:
                    print(f"  Migrated: {doc_id} (user_id={user_id}, email={email})")
                migrated += 1
            except Exception as e:
                print(f"  ERROR migrating {doc_id}: {e}", file=sys.stderr)
                errors += 1

    return migrated, skipped, errors


def main():
    parser = argparse.ArgumentParser(
        description="Migrate karaoke-decide users from 'users' to 'decide_users' collection"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be migrated without making changes",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print detailed information for each document",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Karaoke Decide User Migration")
    print(f"  Source: {SOURCE_COLLECTION}")
    print(f"  Target: {TARGET_COLLECTION}")
    print(f"  Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print("=" * 60)
    print()

    if not args.dry_run:
        response = input("This will copy documents to the new collection. Continue? [y/N]: ")
        if response.lower() != "y":
            print("Aborted.")
            sys.exit(0)

    migrated, skipped, errors = migrate_users(dry_run=args.dry_run, verbose=args.verbose)

    print()
    print("=" * 60)
    print("Migration Summary")
    print("=" * 60)
    print(f"  Documents migrated: {migrated}")
    print(f"  Documents skipped:  {skipped} (karaoke-gen users)")
    if errors > 0:
        print(f"  Errors:             {errors}")
    print()

    if args.dry_run:
        print("This was a DRY RUN. No changes were made.")
        print("Run without --dry-run to perform the migration.")
    else:
        print(f"Successfully migrated {migrated} documents to '{TARGET_COLLECTION}'.")
        print()
        print("Next steps:")
        print("  1. Create Firestore indexes for decide_users collection")
        print("  2. Update backend code to use decide_users")
        print("  3. Deploy and verify")
        print("  4. (Optional) Delete old karaoke-decide docs from users collection")


if __name__ == "__main__":
    main()
