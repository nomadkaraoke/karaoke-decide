#!/usr/bin/env python3
"""
Last.fm User Data Import Script

Imports listening history from ~10,000 Last.fm users to bootstrap collaborative
filtering recommendations. All API responses are cached to GCS for resilience.

Features:
- GCS-backed request cache (never duplicate API calls)
- Resumable at any point without data loss
- Rate limiting (1 req/sec conservative)
- Progress tracking with ETA
- Error handling with exponential backoff

Usage:
    # Full import (discovery + artist fetching)
    python scripts/lastfm_import.py --api-key YOUR_KEY

    # Resume from where we left off
    python scripts/lastfm_import.py --api-key YOUR_KEY --resume

    # Only run discovery phase
    python scripts/lastfm_import.py --api-key YOUR_KEY --phase discovery

    # Only run artist fetching phase
    python scripts/lastfm_import.py --api-key YOUR_KEY --phase artists

    # Show current progress
    python scripts/lastfm_import.py --api-key YOUR_KEY --status
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime

import requests
from google.cloud import storage

# Configuration
GCS_BUCKET = "nomadkaraoke-lastfm-cache"
LASTFM_API_BASE = "https://ws.audioscrobbler.com/2.0/"

# Rate limiting: 1 request per second (conservative, Last.fm allows 5/sec)
REQUEST_DELAY = 1.0

# Import targets
TARGET_USERS = 10000
TOP_ARTISTS_LIMIT = 1000
FRIENDS_PER_USER = 50

# Retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF = [2, 4, 8]  # seconds


class GCSCache:
    """GCS-backed cache for API requests."""

    def __init__(self, bucket_name: str):
        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket_name)
        self._local_cache: dict[str, dict] = {}  # In-memory cache for session

    def _get_cache_path(self, method: str, username: str) -> str:
        """Get GCS path for a cached request."""
        # Normalize username for path safety
        safe_username = username.lower().replace("/", "_").replace("\\", "_")
        return f"requests/{method}/{safe_username}.json"

    def get(self, method: str, username: str) -> dict | None:
        """Get cached response if exists."""
        cache_key = f"{method}:{username}"

        # Check in-memory cache first
        if cache_key in self._local_cache:
            return self._local_cache[cache_key]

        # Check GCS
        path = self._get_cache_path(method, username)
        blob = self.bucket.blob(path)

        try:
            if blob.exists():
                content = blob.download_as_text()
                data = json.loads(content)
                self._local_cache[cache_key] = data
                return data
        except Exception as e:
            print(f"    Cache read error: {e}")

        return None

    def set(self, method: str, username: str, request_params: dict, response: dict):
        """Cache a request/response pair."""
        cache_key = f"{method}:{username}"
        path = self._get_cache_path(method, username)

        data = {
            "request": {
                "method": method,
                "params": request_params,
                "timestamp": datetime.now(UTC).isoformat(),
            },
            "response": response,
        }

        # Save to GCS
        blob = self.bucket.blob(path)
        blob.upload_from_string(json.dumps(data, indent=2), content_type="application/json")

        # Update in-memory cache
        self._local_cache[cache_key] = data

    def get_state(self, state_name: str) -> dict | None:
        """Get persisted state from GCS."""
        path = f"state/{state_name}.json"
        blob = self.bucket.blob(path)

        try:
            if blob.exists():
                content = blob.download_as_text()
                return json.loads(content)
        except Exception as e:
            print(f"  State read error for {state_name}: {e}")

        return None

    def save_state(self, state_name: str, data: dict):
        """Save state to GCS."""
        path = f"state/{state_name}.json"
        blob = self.bucket.blob(path)
        blob.upload_from_string(json.dumps(data, indent=2), content_type="application/json")

    def count_cached_requests(self, method: str) -> int:
        """Count cached requests for a method."""
        prefix = f"requests/{method}/"
        blobs = list(self.bucket.list_blobs(prefix=prefix))
        return len(blobs)


class LastFmClient:
    """Last.fm API client with caching and rate limiting."""

    def __init__(self, api_key: str, cache: GCSCache):
        self.api_key = api_key
        self.cache = cache
        self.last_request_time = 0.0
        self.request_count = 0
        self.cache_hits = 0
        self.errors = 0

    def _rate_limit(self):
        """Ensure we don't exceed rate limits."""
        elapsed = time.time() - self.last_request_time
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        self.last_request_time = time.time()

    def _request_with_retry(self, method: str, **params) -> dict | None:
        """Make API request with retry logic."""
        self._rate_limit()
        self.request_count += 1

        params.update(
            {
                "method": method,
                "api_key": self.api_key,
                "format": "json",
            }
        )

        for attempt in range(MAX_RETRIES):
            try:
                response = requests.get(LASTFM_API_BASE, params=params, timeout=30)

                # Rate limit response - back off and retry
                if response.status_code == 429:
                    wait_time = RETRY_BACKOFF[attempt] if attempt < len(RETRY_BACKOFF) else 60
                    print(f"    Rate limited, waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue

                # Server error - retry
                if response.status_code >= 500:
                    wait_time = RETRY_BACKOFF[attempt] if attempt < len(RETRY_BACKOFF) else 60
                    print(f"    Server error {response.status_code}, retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    continue

                response.raise_for_status()
                data = response.json()

                # API-level error
                if "error" in data:
                    error_code = data.get("error")
                    # Permanent errors - don't retry
                    if error_code in [6, 7, 8]:  # User not found, auth error, etc.
                        return {"error": data.get("message", "Unknown error"), "code": error_code}
                    # Transient errors - retry
                    wait_time = RETRY_BACKOFF[attempt] if attempt < len(RETRY_BACKOFF) else 60
                    print(f"    API error {error_code}: {data.get('message')}, retrying...")
                    time.sleep(wait_time)
                    continue

                return data

            except requests.Timeout:
                wait_time = RETRY_BACKOFF[attempt] if attempt < len(RETRY_BACKOFF) else 60
                print(f"    Timeout, retrying in {wait_time}s...")
                time.sleep(wait_time)
                continue

            except requests.RequestException as e:
                self.errors += 1
                if attempt == MAX_RETRIES - 1:
                    return {"error": str(e), "code": -1}
                wait_time = RETRY_BACKOFF[attempt] if attempt < len(RETRY_BACKOFF) else 60
                print(f"    Request error: {e}, retrying in {wait_time}s...")
                time.sleep(wait_time)
                continue

        self.errors += 1
        return {"error": "Max retries exceeded", "code": -1}

    def get_user_info(self, username: str) -> dict | None:
        """Get user info (cached)."""
        # Check cache first
        cached = self.cache.get("user.getInfo", username)
        if cached:
            self.cache_hits += 1
            response = cached.get("response", {})
            if "user" in response:
                return response["user"]
            return None

        # Fetch from API
        data = self._request_with_retry("user.getInfo", user=username)
        if data:
            self.cache.set("user.getInfo", username, {"user": username}, data)
            if "user" in data:
                return data["user"]

        return None

    def get_user_friends(self, username: str, limit: int = FRIENDS_PER_USER) -> list[str]:
        """Get user's friends (cached)."""
        # Check cache first
        cached = self.cache.get("user.getFriends", username)
        if cached:
            self.cache_hits += 1
            response = cached.get("response", {})
            return self._extract_friends(response)

        # Fetch from API
        data = self._request_with_retry("user.getFriends", user=username, limit=limit)
        if data:
            self.cache.set("user.getFriends", username, {"user": username, "limit": limit}, data)
            return self._extract_friends(data)

        return []

    def _extract_friends(self, data: dict) -> list[str]:
        """Extract friend usernames from API response."""
        if not data or "friends" not in data:
            return []

        friends_data = data["friends"]
        if not friends_data or "user" not in friends_data:
            return []

        users = friends_data["user"]
        if isinstance(users, dict):
            users = [users]

        return [u.get("name") for u in users if u.get("name")]

    def get_top_artists(self, username: str, limit: int = TOP_ARTISTS_LIMIT) -> list[dict]:
        """Get user's top artists (cached)."""
        # Check cache first
        cached = self.cache.get("user.getTopArtists", username)
        if cached:
            self.cache_hits += 1
            response = cached.get("response", {})
            return self._extract_artists(response)

        # Fetch from API
        data = self._request_with_retry("user.getTopArtists", user=username, limit=limit)
        if data:
            self.cache.set("user.getTopArtists", username, {"user": username, "limit": limit}, data)
            return self._extract_artists(data)

        return []

    def _extract_artists(self, data: dict) -> list[dict]:
        """Extract artist data from API response."""
        if not data or "topartists" not in data:
            return []

        artists_data = data["topartists"]
        if not artists_data or "artist" not in artists_data:
            return []

        artists = artists_data["artist"]
        if isinstance(artists, dict):
            artists = [artists]

        return [
            {
                "name": a.get("name"),
                "playcount": int(a.get("playcount", 0)),
                "mbid": a.get("mbid"),
            }
            for a in artists
            if a.get("name")
        ]


class ImportProgress:
    """Track and persist import progress."""

    def __init__(self, cache: GCSCache):
        self.cache = cache
        self.started_at: str | None = None
        self.phase = "init"
        self.discovery = {
            "seed_users": [],
            "discovered": 0,
            "queue_size": 0,
            "errors": 0,
        }
        self.artist_fetching = {
            "total": 0,
            "processed": 0,
            "errors": 0,
        }
        self.api_stats = {
            "total_requests": 0,
            "cache_hits": 0,
            "errors": 0,
        }

        # Load existing progress
        self._load()

    def _load(self):
        """Load progress from GCS."""
        data = self.cache.get_state("progress")
        if data:
            self.started_at = data.get("started_at")
            self.phase = data.get("phase", "init")
            self.discovery = data.get("discovery", self.discovery)
            self.artist_fetching = data.get("artist_fetching", self.artist_fetching)
            self.api_stats = data.get("api_stats", self.api_stats)

    def save(self):
        """Save progress to GCS."""
        data = {
            "started_at": self.started_at or datetime.now(UTC).isoformat(),
            "last_updated": datetime.now(UTC).isoformat(),
            "phase": self.phase,
            "discovery": self.discovery,
            "artist_fetching": self.artist_fetching,
            "api_stats": self.api_stats,
        }
        self.cache.save_state("progress", data)

    def update_from_client(self, client: LastFmClient):
        """Update API stats from client."""
        self.api_stats["total_requests"] = client.request_count
        self.api_stats["cache_hits"] = client.cache_hits
        self.api_stats["errors"] = client.errors

    def print_status(self):
        """Print current progress."""
        print("\n" + "=" * 60)
        print("IMPORT PROGRESS")
        print("=" * 60)
        print(f"Phase: {self.phase}")
        print(f"Started: {self.started_at or 'Not started'}")
        print()
        print("Discovery:")
        print(f"  Seed users: {self.discovery['seed_users']}")
        print(f"  Users discovered: {self.discovery['discovered']}")
        print(f"  Queue size: {self.discovery['queue_size']}")
        print(f"  Errors: {self.discovery['errors']}")
        print()
        print("Artist Fetching:")
        print(f"  Total users: {self.artist_fetching['total']}")
        print(f"  Processed: {self.artist_fetching['processed']}")
        print(f"  Remaining: {self.artist_fetching['total'] - self.artist_fetching['processed']}")
        print(f"  Errors: {self.artist_fetching['errors']}")
        print()
        print("API Stats:")
        print(f"  Total requests: {self.api_stats['total_requests']}")
        print(f"  Cache hits: {self.api_stats['cache_hits']}")
        print(f"  Errors: {self.api_stats['errors']}")
        print("=" * 60)


def run_discovery(
    client: LastFmClient,
    cache: GCSCache,
    progress: ImportProgress,
    seed_users: list[str],
    max_users: int = TARGET_USERS,
) -> set[str]:
    """
    Phase 1: Discover users via friend graph traversal.

    Returns set of discovered usernames.
    """
    progress.phase = "discovery"
    progress.started_at = progress.started_at or datetime.now(UTC).isoformat()
    progress.discovery["seed_users"] = seed_users

    # Load existing discovered users
    discovered_data = cache.get_state("discovered_users")
    discovered = set(discovered_data.get("users", [])) if discovered_data else set()

    # Load queue state
    queue_data = cache.get_state("discovery_queue")
    if queue_data:
        to_visit = [(u["username"], u["depth"]) for u in queue_data.get("queue", [])]
        visited = set(queue_data.get("visited", []))
    else:
        to_visit = [(u, 0) for u in seed_users]
        visited = set()

    print(f"\n🔍 Discovery phase (target: {max_users} users)")
    print(f"   Already discovered: {len(discovered)}")
    print(f"   Queue size: {len(to_visit)}")

    last_save_time = time.time()
    save_interval = 30  # Save state every 30 seconds

    while to_visit and len(discovered) < max_users:
        username, depth = to_visit.pop(0)

        if username in visited:
            continue
        visited.add(username)

        # Check/fetch user info
        print(f"  [{len(discovered)}/{max_users}] Checking {username} (depth {depth})...", end=" ")
        user_info = client.get_user_info(username)

        if user_info and "error" not in user_info:
            discovered.add(username)
            playcount = user_info.get("playcount", "N/A")
            print(f"✓ (playcount: {playcount})")

            # Get friends if we need more users
            if len(discovered) < max_users and depth < 3:
                friends = client.get_user_friends(username)
                new_friends = [f for f in friends if f not in visited and f not in [u for u, _ in to_visit]]
                for friend in new_friends[:20]:  # Limit friends added per user
                    to_visit.append((friend, depth + 1))
                if new_friends:
                    print(f"       Added {len(new_friends[:20])} friends to queue")
        else:
            progress.discovery["errors"] += 1
            print("✗ (invalid/private)")

        # Update progress
        progress.discovery["discovered"] = len(discovered)
        progress.discovery["queue_size"] = len(to_visit)
        progress.update_from_client(client)

        # Periodic save
        if time.time() - last_save_time > save_interval:
            print("       [Saving state...]")
            cache.save_state("discovered_users", {"users": list(discovered)})
            cache.save_state(
                "discovery_queue",
                {
                    "queue": [{"username": u, "depth": d} for u, d in to_visit],
                    "visited": list(visited),
                },
            )
            progress.save()
            last_save_time = time.time()

        # Progress update every 100 users
        if len(discovered) % 100 == 0 and len(discovered) > 0:
            print(f"\n   === Progress: {len(discovered)} discovered, {len(to_visit)} in queue ===\n")

    # Final save
    cache.save_state("discovered_users", {"users": list(discovered)})
    cache.save_state("discovery_queue", {"queue": [], "visited": list(visited)})
    progress.save()

    print(f"\n✓ Discovery complete: {len(discovered)} users found")
    return discovered


def run_artist_fetching(
    client: LastFmClient,
    cache: GCSCache,
    progress: ImportProgress,
) -> dict[str, list[dict]]:
    """
    Phase 2: Fetch top artists for all discovered users.

    Returns dict mapping username to artist list.
    """
    progress.phase = "artist_fetching"

    # Load discovered users
    discovered_data = cache.get_state("discovered_users")
    if not discovered_data:
        print("❌ No discovered users found. Run discovery phase first.")
        return {}

    all_users = discovered_data.get("users", [])

    # Load already processed users
    processed_data = cache.get_state("processed_users")
    processed = set(processed_data.get("users", [])) if processed_data else set()

    # Users still to process
    to_process = [u for u in all_users if u not in processed]

    progress.artist_fetching["total"] = len(all_users)
    progress.artist_fetching["processed"] = len(processed)

    print("\n🎵 Artist fetching phase")
    print(f"   Total users: {len(all_users)}")
    print(f"   Already processed: {len(processed)}")
    print(f"   Remaining: {len(to_process)}")

    if not to_process:
        print("   All users already processed!")
        return {}

    results: dict[str, list[dict]] = {}
    last_save_time = time.time()
    save_interval = 60  # Save every minute

    for i, username in enumerate(to_process):
        print(f"  [{len(processed) + i + 1}/{len(all_users)}] {username}...", end=" ")

        artists = client.get_top_artists(username)
        if artists:
            results[username] = artists
            processed.add(username)
            print(f"✓ {len(artists)} artists")
        else:
            progress.artist_fetching["errors"] += 1
            processed.add(username)  # Still mark as processed to avoid retry
            print("✗ No data")

        # Update progress
        progress.artist_fetching["processed"] = len(processed)
        progress.update_from_client(client)

        # Periodic save
        if time.time() - last_save_time > save_interval:
            print("       [Saving state...]")
            cache.save_state("processed_users", {"users": list(processed)})
            progress.save()
            last_save_time = time.time()

        # Progress update every 100 users
        if (len(processed) + i + 1) % 100 == 0:
            remaining = len(all_users) - len(processed) - i - 1
            eta_seconds = remaining * REQUEST_DELAY
            eta_hours = eta_seconds / 3600
            print(f"\n   === Progress: {len(processed) + i + 1}/{len(all_users)} | ETA: {eta_hours:.1f}h ===\n")

    # Final save
    cache.save_state("processed_users", {"users": list(processed)})
    progress.save()

    print(f"\n✓ Artist fetching complete: {len(results)} users with data")
    return results


def main():
    parser = argparse.ArgumentParser(description="Last.fm User Data Import")
    parser.add_argument("--api-key", required=True, help="Last.fm API key")
    parser.add_argument(
        "--seed-users",
        default="beveradb",
        help="Comma-separated seed usernames (default: beveradb)",
    )
    parser.add_argument(
        "--max-users",
        type=int,
        default=TARGET_USERS,
        help=f"Maximum users to discover (default: {TARGET_USERS})",
    )
    parser.add_argument(
        "--phase",
        choices=["discovery", "artists", "all"],
        default="all",
        help="Which phase to run (default: all)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show current progress and exit",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from last saved state",
    )

    args = parser.parse_args()

    # Initialize GCS cache
    print("Initializing GCS cache...")
    cache = GCSCache(GCS_BUCKET)

    # Load/create progress tracker
    progress = ImportProgress(cache)

    # Status only mode
    if args.status:
        progress.print_status()

        # Show cache stats
        print("\nCache Statistics:")
        print(f"  user.getInfo cached: {cache.count_cached_requests('user.getInfo')}")
        print(f"  user.getFriends cached: {cache.count_cached_requests('user.getFriends')}")
        print(f"  user.getTopArtists cached: {cache.count_cached_requests('user.getTopArtists')}")
        return

    # Initialize API client
    client = LastFmClient(args.api_key, cache)

    # Parse seed users
    seed_users = [u.strip() for u in args.seed_users.split(",") if u.strip()]

    print("=" * 60)
    print("Last.fm User Data Import")
    print("=" * 60)
    print(f"Target users: {args.max_users}")
    print(f"Seed users: {seed_users}")
    print(f"Phase: {args.phase}")
    print(f"Resume: {args.resume}")
    print("=" * 60)

    try:
        # Phase 1: Discovery
        if args.phase in ["discovery", "all"]:
            run_discovery(client, cache, progress, seed_users, args.max_users)

        # Phase 2: Artist fetching
        if args.phase in ["artists", "all"]:
            run_artist_fetching(client, cache, progress)

        # Final status
        progress.update_from_client(client)
        progress.save()
        progress.print_status()

    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted! Saving state...")
        progress.update_from_client(client)
        progress.save()
        progress.print_status()
        print("\nResume with: python scripts/lastfm_import.py --api-key YOUR_KEY --resume")
        sys.exit(1)

    except Exception as e:
        print(f"\n\n❌ Error: {e}")
        progress.update_from_client(client)
        progress.save()
        raise


if __name__ == "__main__":
    main()
