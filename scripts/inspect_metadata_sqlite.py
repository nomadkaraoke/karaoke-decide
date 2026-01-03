#!/usr/bin/env python3
"""
Inspect Spotify metadata SQLite databases to understand what data is available.

This script examines the metadata torrent's sqlite databases to:
1. List all tables and their schemas
2. Show row counts
3. Identify what data hasn't been ETL'd to BigQuery yet

Usage:
    python3 inspect_metadata_sqlite.py /path/to/sqlite.zst

    # Or decompress first:
    zstd -d spotify_clean.sqlite3.zst
    python3 inspect_metadata_sqlite.py spotify_clean.sqlite3
"""

import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


def get_table_info(cursor, table_name: str) -> dict:
    """Get schema and row count for a table."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = cursor.fetchall()

    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    row_count = cursor.fetchone()[0]

    return {
        "table": table_name,
        "row_count": row_count,
        "columns": [
            {
                "name": col[1],
                "type": col[2],
                "nullable": not col[3],
                "primary_key": bool(col[5]),
            }
            for col in columns
        ],
    }


def sample_data(cursor, table_name: str, limit: int = 3) -> list:
    """Get sample rows from a table."""
    try:
        cursor.execute(f"SELECT * FROM {table_name} LIMIT {limit}")
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in rows]
    except Exception as e:
        return [{"error": str(e)}]


def inspect_database(db_path: str, show_samples: bool = False):
    """Inspect a SQLite database and print table information."""

    # Handle compressed files
    if db_path.endswith(".zst"):
        print(f"Decompressing {db_path}...", file=sys.stderr)
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
            tmp_path = tmp.name
        subprocess.run(["zstd", "-d", "-o", tmp_path, db_path], check=True)
        db_path = tmp_path
        cleanup = True
    else:
        cleanup = False

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get all tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]

        print(f"\n{'='*60}")
        print(f"Database: {Path(db_path).name}")
        print(f"Tables: {len(tables)}")
        print(f"{'='*60}\n")

        results = []
        for table in tables:
            info = get_table_info(cursor, table)
            results.append(info)

            print(f"\n## {table}")
            print(f"   Rows: {info['row_count']:,}")
            print("   Columns:")
            for col in info["columns"]:
                pk = " (PK)" if col["primary_key"] else ""
                nullable = "" if col["nullable"] else " NOT NULL"
                print(f"      - {col['name']}: {col['type']}{nullable}{pk}")

            if show_samples:
                print("   Sample data:")
                samples = sample_data(cursor, table)
                for sample in samples:
                    # Truncate long values
                    truncated = {k: str(v)[:100] + "..." if len(str(v)) > 100 else v for k, v in sample.items()}
                    print(f"      {json.dumps(truncated, default=str)}")

        conn.close()
        return results

    finally:
        if cleanup:
            import os

            os.unlink(db_path)


def main():
    parser = argparse.ArgumentParser(description="Inspect Spotify metadata SQLite databases")
    parser.add_argument("db_path", help="Path to SQLite database (can be .zst compressed)")
    parser.add_argument("--samples", action="store_true", help="Show sample data from each table")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    results = inspect_database(args.db_path, args.samples)

    if args.json:
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
