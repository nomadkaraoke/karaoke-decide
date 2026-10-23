# Nomad Karaoke Decide Documentation

> **New session?** Start here. This is the single source of truth for project status.

## What Is This?

A karaoke song discovery app that helps users find songs to sing based on their music listening history.

- **Live site:** https://decide.nomadkaraoke.com
- **API:** https://decide.nomadkaraoke.com/api (proxied via Cloudflare Worker)
- **Repo:** github.com/nomadkaraoke/karaoke-decide

## Current Status (2026-01-15)

**Phase:** MLP COMPLETE + Enhanced Recommendations + My Data + Admin Dashboard + Audio Analysis ETL + Quiz Rework + Better Artist Selection + Collaborative Recs + **MBID-First Migration COMPLETE (Phases 1-7)** + MLHD+ Import ✅

### ✅ What's Working
- **Music data imports + ListenBrainz OAuth (2026-10, v0.21.0 Part A, v0.22.0 Part B, mobile Phase 3b):** Part A added screenshot import (Gemini), public Spotify playlists, Spotify/Apple privacy-export aggregates, per-source summaries/remove-data and the token cipher (`DECIDE_TOKEN_KEY`). Part B adds ListenBrainz connect via MetaBrainz OAuth (live), the Spotify-export upload to ListenBrainz, OIDC-verified `/internal/*` routes, and the daily `lb-poll` Cloud Run Job that folds new listens into profiles. Part C adds sync resume/fencing (`stalled` status, queue retries) and a bounded ingest executor that keeps matching off the event loop; Part D adds the local-stack fakes and hardening. Archive: [archive/2026-10-22-phase3b-music-data.md](archive/2026-10-22-phase3b-music-data.md). See [ARCHITECTURE.md § Music Service Connection](ARCHITECTURE.md#2-music-service-connection)
- **Karaoke log (2026-10, v0.19.0, mobile Phase 5 "Tonight"):** every song a user sings is logged in Firestore `karaoke_log`, from kjbox at Nomad nights (server to server, `X-Kjbox-Secret` = Secret Manager `decide-kjbox-secret`) or the mobile app at any other bar, with an optional post-song survey. Sung records count into `user_songs.times_sung` (unmatched songs into `user_karaoke_unresolved`, retried daily on read). "Love it" now marks the same row instead of overwriting it. See [API.md § Karaoke log](API.md#karaoke-log-apimykaraoke-log)
- **Vocal range API (2026-10, v0.24.0, Phase 3c API part):** `GET/PUT/DELETE /api/my/voice`, recs v2 `fit` per song + `singable` filter + `sort=fit` + `voice` block, public `GET /api/catalog/songs/{song_key}/vocal-range`. Fit is null until the next catalog build adds `vr_*` to prod catalog.db. See [VOCAL-RANGE.md](VOCAL-RANGE.md)
- **Vocal range data (2026-10, v0.22.0, Phase 3c data part):** weekly `mb-refresh` step merges UltraStar notes, pyin measurements (gen stems via the on-demand `vocal-range-gen` job, NomadPC), Gemini estimates and Wikidata voice types into `song_vocal_range` / `artist_vocal_range` and `vr_*` columns in catalog.db / offline.db. Needs `pulumi up` + job execution after merge. See [VOCAL-RANGE.md](VOCAL-RANGE.md)
- **Web on gen sign-in + recommendations v2 (2026-10, v0.18.0, Phase 3a):** the web app signs in with the Nomad (gen) email code (optional Google), so the web, mobile and gen share one account; guest sessions are removed from the web and a guest's data moves over via `POST /api/auth/claim-guest` on sign-in. The recommendations page now runs on `GET /api/my/recommendations/v2` (every filter, sort, library). Local dev: `scripts/local-stack.sh` runs the main API (:8030) + search (:8020) on gen's Firestore emulator, seeded through the test API (`/api/test/*`, `DECIDE_TEST_API=1`, never mounted in production). `make_it` no longer lists songs that already have a karaoke version (`loose_keys`; `has_karaoke` = availability OR in KaraokeNerds). See [TESTING.md § Playwright against the local stacks](TESTING.md#playwright-against-the-local-stacks)
- **Recommendations v2 + catalog.db (2026-10, v0.17.0):** `GET /api/my/recommendations/v2` serves explained sections and filtered/sorted lists from `catalog.db` (built weekly by `mb-refresh`) + `availability.db`, loaded at startup behind `/api/ready` (2Gi, warm-instance uptime ping). Song feedback (hide/love), quiz progress GET and `library=` filters on search. First `catalog.db` build is manual (see [ARCHITECTURE.md § Recommendations catalog](ARCHITECTURE.md#recommendations-catalog-catalogdb-and-startup))
- **Song search service for the mobile app (2026-10):** `karaoke-decide-search` Cloud Run service (scale-to-zero, `/api/search*` via the Worker) + daily `song-index-build` job (builds song_id/availability/offline indexes from gen's kn-data exports, eval-gated, rolls the service) + public offline-index download. Infra in Pulumi + CI with a parity test; first rollout needs the bootstrap in [ARCHITECTURE.md § Song search](ARCHITECTURE.md#song-search)
- **MBID-First Architecture COMPLETE (2026-01-15):**
  - **BigQuery MusicBrainz tables:** 3.0M artists, 766K tags, 455K Spotify mappings, plus release groups/releases/tracks (albums + release dates, v0.11.0); full typed mirror of every core+derived dump table in the `musicbrainz` dataset (v0.12.0) — refreshed weekly by the `mb-refresh` Cloud Run Job (see DATA-CATALOG.md)
  - **ListenBrainz popularity (2026-09-28):** current listen counts + listeners per artist/recording MBID across 9 stats ranges, plus per-user top artists for collaborative filtering — refreshed from each ListenBrainz export by the `lb-refresh` Cloud Run Job (see DATA-CATALOG.md)
  - **MusicBrainz recordings (Phase 7):** 40.3M recordings, 6.4M ISRCs, 177K karaoke songs linked
  - **MBID search APIs:** `search_artists_mbid()`, `get_artist_by_mbid()`, `lookup_mbids_by_names()`
  - **Recording lookup APIs:** `search_recordings()`, `lookup_recording_by_isrc()`, `get_karaoke_recording_links()`
  - **Quiz stores MBIDs:** `quiz_artist_mbids` array in user documents
  - **Collaborative filtering:** Queries by MBID when available, falls back to name matching
  - **User migration complete:** 16/16 users backfilled with MBIDs (100%)
  - **Public API updated (phases 5-6):** All artist endpoints return MBID-first responses
  - **Frontend updated:** TypeScript types and components use MBID as primary identifier
- **Brand Sync:** UI now matches Nomad Karaoke Generator branding (same colors, typography, layout patterns)
- **Light/Dark Theme:** Full theme toggle support - system preference detection + manual toggle in header
- **Homepage Overhaul:** Problem-focused hero, two user paths (quiz vs data sources), Generator integration section
- **UX Refresh:** Streamlined navigation (Recommendations | Music I Know | Playlists | Settings)
- **Landing Page:** Clean value prop + CTA, smart redirect (quiz if not completed, recommendations if completed)
- **Music I Know:** Unified tabbed view (Artists | Songs | Services) replacing scattered data pages
- **Settings Page:** Profile, preferences, services link, logout, danger zone
- **Quiz Flow:** Enhanced 6-step quiz (Genres → Eras → Preferences → Music You Know → Artists → Email) with 22 genres, multi-decade selection, vocal comfort/crowd pleaser preferences, manual artist entry, smart artist suggestions, and email collection before showing recommendations
- **Better Artist Selection:** Quiz step 5 now has infinite scroll (no more jarring list changes), suggestion reason badges ("Similar to Green Day", "Based on punk"), and a sticky finish bar that's always accessible while scrolling
- **Collaborative Recommendations:** Artists are suggested based on four sources of similarity data:
  - **Last.fm users (Firestore):** 10K+ users with real listening history (MBID-first, ~78% MBID coverage)
  - **MLHD+ (BigQuery):** 1.5M artist pairs from 583K Last.fm users - "Listeners of X also like"
  - **Karaoke singers:** "Singers who like Green Day also chose" - from our own quiz data
  - **Music fans:** "Fans of Green Day also like" - from ListenBrainz API
- **Admin Dashboard:** Internal admin panel for user management, sync job monitoring, system stats
- **Frontend:** Live at decide.nomadkaraoke.com with real-time search
- **Backend API:** Deployed on Cloud Run with BigQuery + Secret Manager integration
- **Authentication:** Magic link auth with JWT tokens, guest sessions for frictionless onboarding
- **Music Services:** Spotify OAuth and Last.fm connection (Phase 3 complete)
- **Async Music Sync:** Background sync via Cloud Tasks with progress tracking
- **Quiz & Recommendations:** Quiz onboarding + recommendation algorithm v1 (Phase 4 complete)
- **Enhanced Recommendations:** Categorized sections (Artists You Know, Create Your Own, Crowd Pleasers) with rich filters
- **Playlists:** Full CRUD for user karaoke playlists (Phase 6 Part 1)
- **Karaoke Links:** YouTube search + Karaoke Generator integration (Phase 6 Part 2)
- **Data:** 275K karaoke songs + 256M Spotify tracks + 230M audio features + 33.5M audio analysis tracks + 325M sections + **2.78M MusicBrainz artists + 37.5M recordings + 5.5M ISRCs + 693K tags + 1.5M MLHD+ artist similarity pairs**
- **CI/Testing:** 185 unit tests, 403 backend tests, E2E tests with Playwright
- **Email Delivery:** Postmark configured for production magic link emails
- **API Proxy:** Cloudflare Worker proxies /api/* to Cloud Run (same-origin, no CORS); /api/search* → search service; /offline-index/* → cached public offline DB
- **Spotify Audio Analysis ETL:** Complete! 33.5M tracks + 325M sections from 3.88TB torrent (backed up to GCS)

### 🚧 Next Up (Post-MLP)
1. **Energy/Tempo Filters** - Use audio analysis data for "high energy karaoke" / "chill karaoke" filtering
2. Analytics and usage tracking
3. Social features (share playlists, follow users)
4. Advanced recommendation tuning
5. Mobile app (React Native)

### 📋 Full Roadmap
See [PLAN.md](PLAN.md) for complete implementation phases.

## Key Documents

| Document | Purpose |
|----------|---------|
| [CONTEXT.md](CONTEXT.md) | **Start here for background** - Why this exists, product vision |
| [PLAN.md](PLAN.md) | Implementation plan with phases and data models |
| [VISION.md](VISION.md) | Product goals and user stories |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design, data schemas, BigQuery setup |
| [BRAND-STYLE-GUIDE.md](BRAND-STYLE-GUIDE.md) | **Brand reference** - Colors, typography, themes for UI work |
| [DATA-CATALOG.md](DATA-CATALOG.md) | **Music data reference** - All BigQuery tables, schemas, queries for LLM agents |
| [MUSICBRAINZ-MIGRATION-PLAN.md](MUSICBRAINZ-MIGRATION-PLAN.md) | **MBID-first migration** - Moving from Spotify IDs to MBIDs as primary |
| [FIRESTORE-IMPORT-PLAN.md](FIRESTORE-IMPORT-PLAN.md) | **Last.fm import** - 10K users with MBID-first schema |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Local setup, testing, deployment |
| [API.md](API.md) | Backend endpoint documentation |
| [TESTING.md](TESTING.md) | **Testing guide** - SOLID, test types, coverage, Playwright |
| [LESSONS-LEARNED.md](LESSONS-LEARNED.md) | Accumulated wisdom - check before starting similar work |

## Quick Reference

### Tech Stack
- **Backend:** Python 3.12, FastAPI, BigQuery, Cloud Run
- **Frontend:** Next.js 16, TypeScript, Tailwind, GitHub Pages
- **Data:** BigQuery (catalog), Firestore (user data - planned)

### Key Commands
```bash
# Run tests
make test

# Start API server locally
make dev

# Search songs via CLI
poetry run karaoke-decide songs search "queen"

# Frontend development
cd frontend && npm run dev
```

### BigQuery Tables
| Table | Rows | Description |
|-------|------|-------------|
| `karaoke_decide.karaokenerds_raw` | 275,809 | Karaoke songs with brand counts |
| `karaoke_decide.spotify_track_artists` | 348M | Track-artist junction (multi-artist) |
| `karaoke_decide.spotify_audio_analysis_sections` | **325M** | Song sections with timing, tempo, key changes |
| `karaoke_decide.spotify_tracks` | 256M | Spotify track metadata |
| `karaoke_decide.spotify_audio_features` | 230M | Audio features (danceability, energy, tempo, key, valence, etc.) |
| `karaoke_decide.spotify_albums` | 59M | Album metadata with release dates |
| `karaoke_decide.spotify_audio_analysis_tracks` | **33.5M** | Track-level audio analysis (tempo, key, confidence) |
| `karaoke_decide.spotify_artists` | 15M | Artist metadata (followers, popularity) |
| `karaoke_decide.spotify_artist_genres` | 2.2M | Artist-genre associations (768 unique genres) |
| `karaoke_decide.spotify_artists_normalized` | 15.4M | Pre-normalized artists with aggregated genres |
| `karaoke_decide.mlhd_artist_similarity` | **1.5M** | MLHD+ artist similarity (from 583K Last.fm users) |
| `karaoke_decide.mbid_spotify_mapping` | 376K | MusicBrainz ID to Spotify ID mapping |

> See [DATA-CATALOG.md](DATA-CATALOG.md) for detailed schemas, queries, and feature ideas.

### Live Endpoints
- `GET /api/health` - Basic health check
- `GET /api/health/deep` - Deep health check (validates Firestore, BigQuery, Cloud Tasks)
- `POST /api/auth/guest` - Create guest session (no auth required)
- `POST /api/auth/magic-link` - Request magic link email
- `POST /api/auth/verify` - Verify token, get JWT
- `POST /api/auth/upgrade` - Upgrade guest to verified (requires guest auth)
- `GET /api/auth/me` - Get current user (requires auth)
- `PUT /api/auth/profile` - Update user profile (requires auth)
- `GET /api/catalog/songs?q=<query>` - Search songs
- `GET /api/catalog/songs/popular?limit=20` - Popular songs
- `GET /api/catalog/songs/{id}/links` - Get karaoke links for a song
- `GET /api/catalog/artists?q=<query>` - Search artists (for autocomplete)
- `GET /api/catalog/artists/index` - Get full artist index for client-side search (~24K artists)
- `GET /api/catalog/stats` - Catalog statistics
- `GET /api/services` - List connected music services
- `POST /api/services/spotify/connect` - Start Spotify OAuth
- `GET /api/services/spotify/callback` - Spotify OAuth callback
- `POST /api/services/lastfm/connect` - Connect Last.fm
- `DELETE /api/services/{type}` - Disconnect service
- `POST /api/services/sync` - Trigger listening history sync
- `GET /api/services/sync/status` - Get sync status
- `GET /api/quiz/artists` - Get quiz artists for onboarding (supports genre filtering)
- `POST /api/quiz/artists/smart` - Get smart artist suggestions informed by user preferences
- `GET /api/quiz/decade-artists` - Get example artists per decade
- `GET /api/quiz/songs` - Get quiz songs for onboarding (legacy)
- `POST /api/quiz/submit` - Submit quiz responses (accepts artists, songs, genres, decades, preferences)
- `POST /api/quiz/enjoy-singing` - Submit songs user enjoys singing with metadata
- `GET /api/quiz/status` - Get quiz completion status
- `GET /api/my/songs` - Get user's song library
- `GET /api/my/karaoke-log` - The user's karaoke log (paginated; `since`, `night_id`)
- `PUT /api/my/karaoke-log/{id}` / `DELETE /api/my/karaoke-log/{id}` - Log a song / save its survey / remove it
- `PUT /api/partner/kjbox/karaoke-log/{id}` / `DELETE …` - kjbox history outbox (`X-Kjbox-Secret`)
- `GET /api/known-songs` - Get user's manually-added known songs
- `POST /api/known-songs` - Add a song to known songs
- `POST /api/known-songs/bulk` - Bulk add songs to known songs
- `DELETE /api/known-songs/{id}` - Remove a known song
- `GET /api/my/data/summary` - Get aggregated data summary
- `GET /api/my/data/artists` - Get user's artists from all sources (merged, paginated)
- `POST /api/my/data/artists` - Add artist manually
- `DELETE /api/my/data/artists/{name}` - Remove artist from all sources
- `POST /api/my/data/artists/exclude` - Exclude artist from recommendations
- `DELETE /api/my/data/artists/exclude` - Include artist in recommendations
- `GET /api/my/data/preferences` - Get user preferences
- `PUT /api/my/data/preferences` - Update user preferences
- `GET /api/my/recommendations` - Get personalized recommendations
- `GET /api/my/recommendations/categorized` - Categorized recommendations with rich filters
- `GET /api/playlists` - List user's playlists
- `POST /api/playlists` - Create new playlist
- `GET /api/playlists/{id}` - Get playlist by ID
- `PUT /api/playlists/{id}` - Update playlist
- `DELETE /api/playlists/{id}` - Delete playlist
- `POST /api/playlists/{id}/songs` - Add song to playlist
- `DELETE /api/playlists/{id}/songs/{song_id}` - Remove song from playlist
- `GET /api/admin/stats` - Admin dashboard stats (admin only)
- `GET /api/admin/users` - List all users with pagination (admin only)
- `GET /api/admin/users/{id}` - Get user detail (admin only)
- `GET /api/admin/sync-jobs` - List sync jobs (admin only)
- `GET /api/admin/sync-jobs/{id}` - Get sync job detail (admin only)
- `POST /api/admin/impersonate` - Generate JWT to impersonate a user (admin only)

## For AI Agents

### Before Starting Work
1. Read [/CLAUDE.md](/CLAUDE.md) for **rules** (mandatory git worktrees per agent session, testing, code patterns)
2. Read [CONTEXT.md](CONTEXT.md) for project background
3. Check [PLAN.md](PLAN.md) for what's done vs what's planned
4. Review [LESSONS-LEARNED.md](LESSONS-LEARNED.md) for gotchas

### After Completing Work
1. Update this file's status section if things changed
2. Add entries to [LESSONS-LEARNED.md](LESSONS-LEARNED.md) for new discoveries
3. Create `docs/archive/YYYY-MM-DD-topic.md` for significant work

### Available Slash Commands
- `/plan` - Create implementation plan for a feature
- `/implement` - Implement from a plan
- `/test` - Run tests and report results
- `/docs-review` - Check docs before merging PR
- `/docs-maintain` - Periodic documentation maintenance
- `/coderabbit-review` - Address CodeRabbit PR comments

## Recent Work

| Date | Summary | Archive |
|------|---------|---------|
| 2026-01-14 | **MBID-First Complete Migration** (2.78M artists, 693K tags, user migration, MBID search APIs) | [archive/2026-01-14-mbid-complete-migration.md](archive/2026-01-14-mbid-complete-migration.md) |
| 2026-01-14 | **MLHD+ Import** (1.5M artist similarity pairs from 583K Last.fm users, BigQuery integration) | [archive/2026-01-13-mlhd-import.md](archive/2026-01-13-mlhd-import.md) |
| 2026-01-10 | Quiz Rework (5-step flow, 22 genres, multi-decade, vocal comfort/crowd pleaser prefs, smart artists) | [archive/2026-01-10-quiz-rework.md](archive/2026-01-10-quiz-rework.md) |
| 2026-01-08 | Brand Sync & Theme Toggle (Generator branding, light/dark mode, homepage overhaul) | [archive/2026-01-08-brand-sync-theme-toggle.md](archive/2026-01-08-brand-sync-theme-toggle.md) |
| 2026-01-04 | Enhanced Artists Tab (merged sources, pagination, exclusions, compact UI) | [archive/2026-01-04-enhanced-artists-tab.md](archive/2026-01-04-enhanced-artists-tab.md) |
| 2026-01-04 | Onboarding Flow Fix (quiz bypass bug, quiz completion tracking, quiz prompt banner) | [archive/2026-01-04-onboarding-flow-fix.md](archive/2026-01-04-onboarding-flow-fix.md) |
| 2026-01-03 | UX Refresh (landing page, navigation, quiz streamlining, Music I Know, Settings) | [archive/2026-01-03-ux-refresh.md](archive/2026-01-03-ux-refresh.md) |
| 2026-01-03 | Admin Dashboard (user management, sync job monitoring, system stats) | [archive/2026-01-03-admin-dashboard.md](archive/2026-01-03-admin-dashboard.md) |
| 2026-01-02 | User Data UX (My Data page reorg, playcount pills, "Artists/Songs You Know", improved sorting) | [archive/2026-01-02-user-data-ux-improvements.md](archive/2026-01-02-user-data-ux-improvements.md) |
| 2026-01-02 | Cloudflare API Proxy (eliminates CORS, same-origin /api/* requests via Worker) | [archive/2026-01-02-cloudflare-api-proxy.md](archive/2026-01-02-cloudflare-api-proxy.md) |
| 2026-01-02 | Known Songs (manual song entry for songs users know they like singing) | [archive/2026-01-02-known-songs-feature.md](archive/2026-01-02-known-songs-feature.md) |
| 2026-01-02 | My Data Page (unified data management, replaces My Songs + Services, editable preferences) | [archive/2026-01-02-my-data-page.md](archive/2026-01-02-my-data-page.md) |
| 2026-01-02 | Quiz UX Improvements (genre selection step, refresh artists, connect CTA, data-testid) | [archive/2026-01-02-quiz-ux-improvements.md](archive/2026-01-02-quiz-ux-improvements.md) |
| 2026-01-02 | Guest User Onboarding (frictionless onboarding, guest sessions, upgrade flow) | [archive/2026-01-02-guest-onboarding-flow.md](archive/2026-01-02-guest-onboarding-flow.md) |
| 2026-01-09 | **Full Spotify ETL Complete** (33.5M tracks, 325M sections from 3.88TB torrent, VM deleted) | [archive/2026-01-02-full-spotify-etl.md](archive/2026-01-02-full-spotify-etl.md) |
| 2026-01-02 | Audio Analysis ETL Setup (3.45 TiB preserved in GCS Archive) | [archive/2025-01-01-spotify-audio-analysis-etl-setup.md](archive/2025-01-01-spotify-audio-analysis-etl-setup.md) |
| 2026-01-01 | Quiz UX V2 (15 inclusive genres, genre filtering, "Show More Artists", genre pills, decade examples, ETL script) | [archive/2026-01-01-quiz-ux-v2.md](archive/2026-01-01-quiz-ux-v2.md) |
| 2026-01-01 | Enhanced Recommendations (categorized sections, artist diversity, rich filters, Create Your Own Karaoke) | [archive/2026-01-01-enhanced-recommendations.md](archive/2026-01-01-enhanced-recommendations.md) |
| 2025-12-31 | Sync IAM Fix & Health Monitoring (403 fix, deep health endpoint, scheduled monitoring, comprehensive E2E) | [archive/2025-12-31-sync-iam-fix-and-health-monitoring.md](archive/2025-12-31-sync-iam-fix-and-health-monitoring.md) |
| 2025-12-31 | **Production Hotfix** (Cloud Run secrets, magic link auth fix) | [archive/2025-12-31-cloud-run-secrets-hotfix.md](archive/2025-12-31-cloud-run-secrets-hotfix.md) |
| 2025-12-31 | Async Music Sync & CodeRabbit Fixes (Cloud Tasks, progress UI, 11 bug fixes) | [archive/2025-12-31-async-sync-coderabbit-fixes.md](archive/2025-12-31-async-sync-coderabbit-fixes.md) |
| 2025-12-30 | **Phase 6: MLP Complete** (Playlists, Karaoke Links, Profile) | [archive/2025-12-30-phase6-mlp-completion.md](archive/2025-12-30-phase6-mlp-completion.md) |
| 2025-12-30 | Phase 5: Frontend Auth & Discovery (auth flow, My Songs, Recommendations, Quiz, Services pages) | [archive/2025-12-30-phase5-frontend-auth-discovery.md](archive/2025-12-30-phase5-frontend-auth-discovery.md) |
| 2024-12-30 | Phase 4: Quiz & Recommendations (quiz onboarding, recommendation algorithm v1) | [archive/2024-12-30-phase4-quiz-recommendations.md](archive/2024-12-30-phase4-quiz-recommendations.md) |
| 2024-12-30 | Phase 3: Music Service Integration (Spotify OAuth, Last.fm, sync) | [archive/2024-12-30-phase3-music-service-integration.md](archive/2024-12-30-phase3-music-service-integration.md) |
| 2024-12-30 | Phase 2: Auth & User Management (magic link, JWT, Firestore) | [archive/2024-12-30-auth-implementation.md](archive/2024-12-30-auth-implementation.md) |
| 2024-12-30 | Comprehensive test coverage (135 unit, 33 backend tests) | [archive/2024-12-30-comprehensive-test-coverage.md](archive/2024-12-30-comprehensive-test-coverage.md) |
| 2024-12-30 | Data foundation (BigQuery ETL) + frontend launch | [archive/2024-12-30-data-foundation-and-frontend.md](archive/2024-12-30-data-foundation-and-frontend.md) |
