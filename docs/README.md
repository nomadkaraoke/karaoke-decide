# Nomad Karaoke Decide Documentation

> **New session?** Start here. This is the single source of truth for project status.

## What Is This?

A karaoke song discovery app that helps users find songs to sing based on their music listening history.

- **Live site:** https://decide.nomadkaraoke.com
- **API:** https://karaoke-decide-718638054799.us-central1.run.app
- **Repo:** github.com/nomadkaraoke/karaoke-decide

## Current Status (2024-12-30)

**Phase:** ~35% to MLP (Minimum Lovable Product)

### ✅ What's Working
- **Frontend:** Live at decide.nomadkaraoke.com with real-time search
- **Backend API:** Deployed on Cloud Run with BigQuery integration
- **Authentication:** Magic link auth with JWT tokens (Phase 2 complete)
- **Data:** 275K karaoke songs + 256M Spotify tracks loaded
- **CI/Testing:** 135 unit tests (99% coverage), 63 backend tests (83% coverage), all checks passing

### 🚧 Next Up (Phase 3: Music Service Integration)
1. Spotify OAuth flow
2. Spotify listening history fetch
3. Last.fm API integration
4. Background sync job

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
- `POST /api/auth/magic-link` - Request magic link email
- `POST /api/auth/verify` - Verify token, get JWT
- `GET /api/auth/me` - Get current user (requires auth)
- `GET /api/catalog/songs?q=<query>` - Search songs
- `GET /api/catalog/songs/popular?limit=20` - Popular songs
- `GET /api/catalog/stats` - Catalog statistics

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
| 2024-12-30 | Phase 2: Auth & User Management (magic link, JWT, Firestore) | [archive/2024-12-30-auth-implementation.md](archive/2024-12-30-auth-implementation.md) |
| 2024-12-30 | Comprehensive test coverage (135 unit, 33 backend tests) | [archive/2024-12-30-comprehensive-test-coverage.md](archive/2024-12-30-comprehensive-test-coverage.md) |
| 2024-12-30 | Data foundation (BigQuery ETL) + frontend launch | [archive/2024-12-30-data-foundation-and-frontend.md](archive/2024-12-30-data-foundation-and-frontend.md) |
