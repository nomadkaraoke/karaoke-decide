# MBID-First Complete Migration

**Date:** 2026-01-14
**Status:** Complete
**PR:** TBD

## Summary

Completed the migration from Spotify IDs to MusicBrainz IDs (MBIDs) as the primary identifier for music entities. This establishes MBID-first architecture across the system while maintaining Spotify data as enrichment.

## What Was Done

### 1. MusicBrainz ETL Pipeline

Created `scripts/musicbrainz_etl.py` - a full ETL pipeline for MusicBrainz database dumps:
- Downloads raw dumps from GCS (6.5GB `mbdump.tar.bz2`, 453MB `mbdump-derived.tar.bz2`)
- Streams and extracts data without full decompression
- Transforms PostgreSQL dump format to NDJSON
- Loads to BigQuery with proper schemas

**Commands:**
- `download` - Download raw dumps from GCS
- `extract` - Extract artists, tags, mappings to NDJSON
- `transform` - Transform NDJSON for BigQuery loading
- `load` - Load to BigQuery tables
- `all` - Run full pipeline

### 2. BigQuery Tables Created

| Table | Rows | Description |
|-------|------|-------------|
| `mb_artists` | 2,780,016 | Full MusicBrainz artist catalog |
| `mb_artist_tags` | 693,045 | Community-sourced tags/genres |
| `mbid_spotify_mapping` | 376,231 | MBID to Spotify ID mappings |
| `mb_artists_normalized` | 2,780,016 | Pre-joined for fast search |

### 3. MBID Search APIs

Added to `karaoke_decide/services/bigquery_catalog.py`:
- `ArtistSearchResultMBID` dataclass for typed results
- `search_artists_mbid()` - Search with MBID-first results
- `get_artist_by_mbid()` - Single artist lookup by MBID
- `lookup_mbids_by_names()` - Batch name→MBID resolution
- `lookup_mbid_by_spotify_id()` - Spotify→MBID mapping

### 4. Quiz Service Integration

Updated `backend/services/quiz_service.py`:
- Quiz submission now resolves artist names to MBIDs
- Stores `quiz_artist_mbids` array in user documents
- Enriches `quiz_manual_artists` with MBID field
- Collaborative filtering queries by MBID when available

### 5. User Migration Script

Created `scripts/migrate_users_to_mbid.py`:
- Backfills `quiz_artist_mbids` for existing users
- Enriches `quiz_manual_artists` with MBIDs
- Tracks migration status and progress

**Results:**
- 16/16 users migrated (100%)
- 73 total MBIDs resolved across all users

## Technical Decisions

### Why Stream from tar.bz2?
MusicBrainz dumps are ~6.5GB compressed. Extracting fully requires ~50GB disk. Streaming from tar directly allows processing on smaller disks while maintaining single-pass efficiency.

### Why Pre-Joined Normalized Table?
Creating `mb_artists_normalized` with pre-joined Spotify data and aggregated tags enables fast prefix search without expensive JOINs at query time. Popularity from Spotify enriches ranking.

### Bug Fixes During Implementation

1. **Tag extraction bug**: `endswith("tag")` matched both "tag" and "area_tag" files. Fixed by checking exact filename or `/`-prefixed path.

2. **Empty array falsy bug**: `if not data.get("quiz_artist_mbids")` treated empty `[]` as needing migration. Fixed by checking field existence: `if "quiz_artist_mbids" not in data`.

## Files Changed

| File | Changes |
|------|---------|
| `scripts/musicbrainz_etl.py` | NEW - Full ETL pipeline |
| `scripts/migrate_users_to_mbid.py` | NEW - User migration script |
| `karaoke_decide/services/bigquery_catalog.py` | Added MBID search methods |
| `backend/services/quiz_service.py` | Store and query by MBIDs |
| `docs/README.md` | Updated status |
| `docs/MUSICBRAINZ-MIGRATION-PLAN.md` | Marked phases complete |
| `docs/DATA-CATALOG.md` | Music Data Catalog with all BigQuery tables |

## Verification

```bash
# Check BigQuery tables
bq show karaoke_decide.mb_artists
bq show karaoke_decide.mb_artist_tags

# Verify row counts
bq query "SELECT COUNT(*) FROM karaoke_decide.mb_artists"
# 2,780,016

bq query "SELECT COUNT(*) FROM karaoke_decide.mb_artist_tags"
# 693,045

# Check user migration
python scripts/migrate_users_to_mbid.py --status
# 16/16 migrated (100%)
```

## What's Next

- **Frontend MBID integration**: Update TypeScript interfaces to use MBID
- **MusicBrainz refresh automation**: Set up weekly dump refresh
- **MLHD+ integration**: Already done by another agent (1.5M artist pairs)

## Related PRs

- #92 - MBID-first architecture for collaborative filtering
- #93 - MLHD+ artist similarity integration
