# 2024-12-30: Data Foundation & Frontend Launch

## Summary

Completed Phase 1 (Data Foundation) and partial Phase 8 (Web Frontend) of the Nomad Karaoke Decide project. The app is now live at https://decide.nomadkaraoke.com with real-time search across 275K karaoke songs.

## What Was Done

### Data Pipeline
1. **KaraokeNerds Catalog** - Loaded 275,809 karaoke songs to BigQuery
   - Table: `nomadkaraoke.karaoke_decide.karaokenerds_raw`
   - Includes brand count as popularity signal

2. **Spotify Metadata** - Loaded 256,039,007 tracks to BigQuery
   - Table: `nomadkaraoke.karaoke_decide.spotify_tracks`
   - 198M tracks have ISRC codes for future karaoke matching
   - Includes popularity scores, artist metadata

3. **ETL Process**
   - Downloaded 186GB Spotify torrent to GCE VM
   - Decompressed 36GB .zst to 117GB SQLite
   - Exported 513 NDJSON files (20GB compressed)
   - Loaded to BigQuery via GCS

### Backend API
- FastAPI backend with BigQuery integration
- Endpoints: `/api/catalog/songs`, `/api/catalog/songs/popular`, `/api/catalog/stats`
- Deployed to Cloud Run: `karaoke-decide-718638054799.us-central1.run.app`
- CORS configured for decide.nomadkaraoke.com

### Frontend
- Next.js 16 with App Router, TypeScript, Tailwind
- Neon-noir karaoke theme (dark with pink/cyan/purple neons)
- Mobile-first responsive design
- Real-time search with debouncing
- Deployed to GitHub Pages with custom domain

## Key Files Created

```
karaoke_decide/services/bigquery_catalog.py  # BigQuery service
backend/api/routes/catalog.py                # API endpoints
frontend/src/app/page.tsx                    # Main search UI
frontend/src/app/globals.css                 # Neon theme
scripts/spotify_to_bigquery.py               # ETL script
.github/workflows/deploy-frontend.yml        # GitHub Pages CI/CD
```

## Infrastructure

- **BigQuery**: Song catalog storage and search
- **Cloud Run**: Backend API hosting
- **GitHub Pages**: Frontend hosting
- **GCS**: Staging for BigQuery loads (`gs://nomadkaraoke-data/`)

## What's Next

1. User authentication (magic link)
2. Spotify/Last.fm OAuth integration
3. Quiz-based onboarding for users without streaming data
4. Playlist creation and management
5. Match Spotify tracks to karaoke songs via ISRC

## Metrics

- KaraokeNerds songs: 275,809
- Spotify tracks: 256,039,007
- Tracks with ISRC: 197,773,424
- Unique Spotify artists: 9,024,440
- Most popular karaoke song: "My Heart Will Go On" (62 brands)
