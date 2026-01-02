# Nomad Karaoke Decide Documentation

> **New session?** Start here. This is the single source of truth for project status.

## What Is This?

A karaoke song discovery app that helps users find songs to sing based on their music listening history.

- **Live site:** https://decide.nomadkaraoke.com
- **API:** https://karaoke-decide-718638054799.us-central1.run.app
- **Repo:** github.com/nomadkaraoke/karaoke-decide

## Current Status (2026-01-01)

**Phase:** MLP COMPLETE + Enhanced Recommendations

### ✅ What's Working
- **Frontend:** Live at decide.nomadkaraoke.com with real-time search
- **Backend API:** Deployed on Cloud Run with BigQuery + Secret Manager integration
- **Authentication:** Magic link auth with JWT tokens, guest sessions for frictionless onboarding
- **Music Services:** Spotify OAuth and Last.fm connection (Phase 3 complete)
- **Async Music Sync:** Background sync via Cloud Tasks with progress tracking
- **Quiz & Recommendations:** Quiz onboarding + recommendation algorithm v1 (Phase 4 complete)
- **Enhanced Recommendations:** Categorized sections (Artists You Know, Create Your Own, Crowd Pleasers) with rich filters
- **Frontend Auth & Discovery:** Full auth flow, My Songs, Recommendations, Quiz UI, Services page (Phase 5 complete)
- **Playlists:** Full CRUD for user karaoke playlists (Phase 6 Part 1)
- **Karaoke Links:** YouTube search + Karaoke Generator integration (Phase 6 Part 2)
- **User Profile:** Profile settings page with display name management (Phase 6 Part 3)
- **Data:** 275K karaoke songs + 256M Spotify tracks loaded
- **CI/Testing:** 135 unit tests, 280+ backend tests, 67 E2E tests, all checks passing
- **Email Delivery:** SendGrid configured for production magic link emails

### 🚧 Next Up (Post-MLP)
1. Analytics and usage tracking
2. Social features (share playlists, follow users)
3. Advanced recommendation tuning
4. Mobile app (React Native)

### 📋 Full Roadmap
See [PLAN.md](PLAN.md) for complete implementation phases.

## Key Documents

| Document | Purpose |
|----------|---------|
| [CONTEXT.md](CONTEXT.md) | **Start here for background** - Why this exists, product vision |
| [PLAN.md](PLAN.md) | Implementation plan with phases and data models |
| [VISION.md](VISION.md) | Product goals and user stories |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design, data schemas, BigQuery setup |
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
| `karaoke_decide.spotify_tracks` | 256,039,007 | Spotify track metadata |

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
- `GET /api/catalog/stats` - Catalog statistics
- `GET /api/services` - List connected music services
- `POST /api/services/spotify/connect` - Start Spotify OAuth
- `GET /api/services/spotify/callback` - Spotify OAuth callback
- `POST /api/services/lastfm/connect` - Connect Last.fm
- `DELETE /api/services/{type}` - Disconnect service
- `POST /api/services/sync` - Trigger listening history sync
- `GET /api/services/sync/status` - Get sync status
- `GET /api/quiz/songs` - Get quiz songs for onboarding
- `POST /api/quiz/submit` - Submit quiz responses
- `GET /api/quiz/status` - Get quiz completion status
- `GET /api/my/songs` - Get user's song library
- `GET /api/my/recommendations` - Get personalized recommendations
- `GET /api/my/recommendations/categorized` - Categorized recommendations with rich filters
- `GET /api/playlists` - List user's playlists
- `POST /api/playlists` - Create new playlist
- `GET /api/playlists/{id}` - Get playlist by ID
- `PUT /api/playlists/{id}` - Update playlist
- `DELETE /api/playlists/{id}` - Delete playlist
- `POST /api/playlists/{id}/songs` - Add song to playlist
- `DELETE /api/playlists/{id}/songs/{song_id}` - Remove song from playlist

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
| 2026-01-02 | Guest User Onboarding (frictionless onboarding, guest sessions, upgrade flow) | [archive/2026-01-02-guest-onboarding-flow.md](archive/2026-01-02-guest-onboarding-flow.md) |
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
