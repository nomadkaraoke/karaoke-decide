#!/usr/bin/env python3
"""
Last.fm User Data Import Test Script

Tests the hypothesis that we can bootstrap collaborative filtering data
by importing Last.fm user listening history via their public API.

Strategy:
1. Start with seed usernames (found from public sources)
2. Recursively expand via user.getFriends
3. Fetch top artists for each user
4. Save to JSON files for analysis

Rate limit compliance: Last.fm ToS allows ~5 req/sec, we'll be conservative
and use 1 request per 0.25 seconds (4 req/sec max).

Usage:
    python scripts/lastfm_import_test.py --api-key YOUR_API_KEY
    python scripts/lastfm_import_test.py --api-key YOUR_API_KEY --seed-users user1,user2
"""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import requests

# Rate limiting: 4 requests per second max (being conservative)
REQUEST_DELAY = 0.25  # seconds between requests

# API configuration
LASTFM_API_BASE = "https://ws.audioscrobbler.com/2.0/"


class LastFmClient:
    """Simple Last.fm API client with rate limiting."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.last_request_time = 0
        self.request_count = 0

    def _rate_limit(self):
        """Ensure we don't exceed rate limits."""
        elapsed = time.time() - self.last_request_time
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        self.last_request_time = time.time()
        self.request_count += 1

    def _request(self, method: str, **params) -> dict | None:
        """Make a rate-limited API request."""
        self._rate_limit()

        params.update(
            {
                "method": method,
                "api_key": self.api_key,
                "format": "json",
            }
        )

        try:
            response = requests.get(LASTFM_API_BASE, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            # Check for API errors
            if "error" in data:
                print(f"  API error: {data.get('message', 'Unknown error')}")
                return None

            return data
        except requests.RequestException as e:
            print(f"  Request error: {e}")
            return None

    def get_user_info(self, username: str) -> dict | None:
        """Get basic info about a user."""
        data = self._request("user.getInfo", user=username)
        if data and "user" in data:
            return data["user"]
        return None

    def get_user_friends(self, username: str, limit: int = 50) -> list[str]:
        """Get a user's friends (usernames only)."""
        data = self._request("user.getFriends", user=username, limit=limit)
        if not data or "friends" not in data:
            return []

        friends_data = data["friends"]
        if not friends_data or "user" not in friends_data:
            return []

        users = friends_data["user"]
        # Handle single friend case (API returns dict instead of list)
        if isinstance(users, dict):
            users = [users]

        return [u.get("name") for u in users if u.get("name")]

    def get_top_artists(self, username: str, limit: int = 100) -> list[dict]:
        """Get a user's top artists."""
        data = self._request("user.getTopArtists", user=username, limit=limit)
        if not data or "topartists" not in data:
            return []

        artists_data = data["topartists"]
        if not artists_data or "artist" not in artists_data:
            return []

        artists = artists_data["artist"]
        # Handle single artist case
        if isinstance(artists, dict):
            artists = [artists]

        return [
            {
                "name": a.get("name"),
                "playcount": int(a.get("playcount", 0)),
                "mbid": a.get("mbid"),  # MusicBrainz ID if available
            }
            for a in artists
            if a.get("name")
        ]


def discover_users(
    client: LastFmClient,
    seed_users: list[str],
    max_users: int = 100,
    max_depth: int = 3,
) -> set[str]:
    """
    Discover users by recursively fetching friends.

    Args:
        client: Last.fm API client
        seed_users: Starting usernames
        max_users: Maximum users to discover
        max_depth: Maximum recursion depth

    Returns:
        Set of discovered usernames
    """
    discovered = set()
    to_visit = [(u, 0) for u in seed_users]  # (username, depth)
    visited = set()

    print(f"\n🔍 Discovering users (max {max_users}, depth {max_depth})...")

    while to_visit and len(discovered) < max_users:
        username, depth = to_visit.pop(0)

        if username in visited:
            continue
        visited.add(username)

        # Verify user exists and add to discovered
        print(f"  Checking {username} (depth {depth})...")
        user_info = client.get_user_info(username)
        if user_info:
            discovered.add(username)
            print(f"    ✓ Valid user: {username} (playcount: {user_info.get('playcount', 'N/A')})")

            # Get friends if not at max depth
            if depth < max_depth:
                friends = client.get_user_friends(username, limit=20)
                print(f"    Found {len(friends)} friends")
                for friend in friends:
                    if friend not in visited and friend not in [u for u, _ in to_visit]:
                        to_visit.append((friend, depth + 1))
        else:
            print("    ✗ Invalid or private user")

        # Progress update
        if len(discovered) % 10 == 0 and len(discovered) > 0:
            print(f"\n  Progress: {len(discovered)} users discovered, {len(to_visit)} in queue\n")

    return discovered


def fetch_user_artists(
    client: LastFmClient,
    usernames: list[str],
    top_n: int = 100,
) -> dict[str, list[dict]]:
    """
    Fetch top artists for each user.

    Args:
        client: Last.fm API client
        usernames: List of usernames to fetch
        top_n: Number of top artists per user

    Returns:
        Dict mapping username to list of artist data
    """
    results = {}

    print(f"\n🎵 Fetching top {top_n} artists for {len(usernames)} users...")

    for i, username in enumerate(usernames):
        print(f"  [{i+1}/{len(usernames)}] {username}...", end=" ")
        artists = client.get_top_artists(username, limit=top_n)
        if artists:
            results[username] = artists
            print(f"✓ {len(artists)} artists")
        else:
            print("✗ No data")

    return results


def save_results(data: dict, output_dir: Path, prefix: str = "lastfm"):
    """Save results to JSON files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save full data
    full_path = output_dir / f"{prefix}_users_{timestamp}.json"
    with open(full_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\n💾 Saved full data to {full_path}")

    # Save summary
    summary = {
        "timestamp": timestamp,
        "total_users": len(data["users"]),
        "users_with_artists": len(data["user_artists"]),
        "total_artist_entries": sum(len(a) for a in data["user_artists"].values()),
        "unique_artists": len(set(artist["name"] for artists in data["user_artists"].values() for artist in artists)),
        "sample_users": list(data["user_artists"].keys())[:10],
    }

    summary_path = output_dir / f"{prefix}_summary_{timestamp}.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"💾 Saved summary to {summary_path}")

    return summary


def main():
    parser = argparse.ArgumentParser(description="Test Last.fm user data import")
    parser.add_argument(
        "--api-key",
        required=True,
        help="Last.fm API key",
    )
    parser.add_argument(
        "--seed-users",
        default="RJ,Bsjelly,Ksjelly",  # Some known active Last.fm users
        help="Comma-separated list of seed usernames",
    )
    parser.add_argument(
        "--max-users",
        type=int,
        default=50,
        help="Maximum users to discover (default: 50)",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=2,
        help="Maximum friend recursion depth (default: 2)",
    )
    parser.add_argument(
        "--top-artists",
        type=int,
        default=100,
        help="Number of top artists per user (default: 100)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/lastfm_import"),
        help="Output directory for JSON files",
    )

    args = parser.parse_args()

    # Parse seed users
    seed_users = [u.strip() for u in args.seed_users.split(",") if u.strip()]

    print("=" * 60)
    print("Last.fm User Data Import Test")
    print("=" * 60)
    print(f"Seed users: {seed_users}")
    print(f"Max users: {args.max_users}")
    print(f"Max depth: {args.max_depth}")
    print(f"Top artists per user: {args.top_artists}")
    print(f"Output dir: {args.output_dir}")
    print("=" * 60)

    # Initialize client
    client = LastFmClient(args.api_key)

    # Step 1: Discover users via friends
    discovered_users = discover_users(
        client,
        seed_users,
        max_users=args.max_users,
        max_depth=args.max_depth,
    )

    if not discovered_users:
        print("\n❌ No users discovered. Check your API key and seed users.")
        return

    print(f"\n✓ Discovered {len(discovered_users)} users")

    # Step 2: Fetch top artists for each user
    user_artists = fetch_user_artists(
        client,
        list(discovered_users),
        top_n=args.top_artists,
    )

    # Step 3: Save results
    data = {
        "seed_users": seed_users,
        "users": list(discovered_users),
        "user_artists": user_artists,
    }

    summary = save_results(data, args.output_dir)

    # Print summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total users discovered: {summary['total_users']}")
    print(f"Users with artist data: {summary['users_with_artists']}")
    print(f"Total artist entries: {summary['total_artist_entries']}")
    print(f"Unique artists: {summary['unique_artists']}")
    print(f"API requests made: {client.request_count}")
    print("=" * 60)

    # Analysis: How useful is this for collaborative filtering?
    if user_artists:
        # Find artists that appear for multiple users
        artist_counts: dict[str, int] = {}
        for artists in user_artists.values():
            for artist in artists:
                name = artist["name"]
                artist_counts[name] = artist_counts.get(name, 0) + 1

        shared_artists = {k: v for k, v in artist_counts.items() if v >= 2}
        print(f"\nArtists liked by 2+ users: {len(shared_artists)}")

        top_shared = sorted(shared_artists.items(), key=lambda x: -x[1])[:20]
        print("\nTop 20 most shared artists:")
        for artist, count in top_shared:
            print(f"  {count} users: {artist}")


if __name__ == "__main__":
    main()
