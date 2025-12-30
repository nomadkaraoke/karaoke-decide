# Nomad Karaoke Decide Documentation

## Current Status

**Phase:** Data Foundation Complete, Frontend Live
**Version:** 0.2.0

### Live
- **Frontend:** https://decide.nomadkaraoke.com (GitHub Pages)
- **API:** Local development (Cloud Run deployment pending)

### Data Loaded
- [x] KaraokeNerds catalog: **275,809 karaoke songs** in BigQuery
- [x] Spotify metadata: **256,039,007 tracks** in BigQuery (198M with ISRC)

### Implemented
- [x] Project structure
- [x] Core data models
- [x] CLI with working song search commands
- [x] FastAPI backend with BigQuery integration
- [x] Catalog API endpoints (search, popular, stats)
- [x] Next.js frontend with neon-noir theme
- [x] GitHub Actions deployment pipeline
- [x] BigQuery ETL scripts for Spotify data

### In Progress
- [ ] Connect frontend to backend API
- [ ] Deploy backend to Cloud Run

### Planned
- [ ] Spotify OAuth integration
- [ ] Last.fm integration
- [ ] User authentication (magic link)
- [ ] Playlist management
- [ ] Song recommendations

## Quick Links

| Document | Description |
|----------|-------------|
| [Architecture](ARCHITECTURE.md) | System design and data flow |
| [Development](DEVELOPMENT.md) | Local setup and testing |
| [API Reference](API.md) | Backend endpoint documentation |

## Getting Started

```bash
# Clone and install
git clone https://github.com/nomadkaraoke/karaoke-decide
cd karaoke-decide
poetry install

# Run tests
make test

# Start API server
make dev

# Run CLI
poetry run karaoke-decide songs search "queen"
poetry run karaoke-decide songs popular --limit 20

# Frontend development
cd frontend && npm install && npm run dev
```

## BigQuery Tables

| Table | Rows | Description |
|-------|------|-------------|
| `karaoke_decide.karaokenerds_raw` | 275,809 | Karaoke songs with brand counts |
| `karaoke_decide.spotify_tracks` | 256,039,007 | Spotify track metadata |
