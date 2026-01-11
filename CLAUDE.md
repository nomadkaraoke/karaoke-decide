# Claude Code Guidelines for Nomad Karaoke Decide

> **New session?** Read [docs/README.md](docs/README.md) first for current status and entry points.

## Quick Reference

| Topic | Location |
|-------|----------|
| **Current status** | **[docs/README.md](docs/README.md)** ← Start here |
| Project background | [docs/CONTEXT.md](docs/CONTEXT.md) |
| Implementation plan | [docs/PLAN.md](docs/PLAN.md) |
| Product vision | [docs/VISION.md](docs/VISION.md) |
| Lessons learned | [docs/LESSONS-LEARNED.md](docs/LESSONS-LEARNED.md) |
| Architecture | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Development setup | [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) |
| API reference | [docs/API.md](docs/API.md) |
| **Testing guide** | [docs/TESTING.md](docs/TESTING.md) |

## Project Overview

**Nomad Karaoke Decide** helps people discover and choose karaoke songs based on their music listening history.

- **Live:** https://decide.nomadkaraoke.com
- **API:** https://karaoke-decide-718638054799.us-central1.run.app
- **Tech Stack:** Python 3.12, FastAPI, BigQuery, Cloud Run, Next.js
- **Sister Project:** [karaoke-gen](https://github.com/nomadkaraoke/karaoke-gen) - shares patterns

## Essential Rules

### Code Quality (SOLID)

All code must follow SOLID principles:
- **Single Responsibility** - Each class/function does one thing
- **Open/Closed** - Open for extension, closed for modification
- **Liskov Substitution** - Subtypes substitutable for base types
- **Interface Segregation** - Specific interfaces over general ones
- **Dependency Inversion** - Depend on abstractions, not concretions

See [docs/TESTING.md](docs/TESTING.md) for full code quality standards.

### Git Workflow

1. **Never commit directly to `main`** - use `/new-worktree <description>` to start
2. **Follow global workflow** - see `~/.claude/CLAUDE.md` for command sequence
3. **PR format:** Summary, changes list, testing info
4. **CI must pass before merge** - lint, unit tests, backend tests, frontend build

### Testing (Mandatory)

**CI blocks merge if tests fail.** Coverage requirements:
- Unit tests: **70%+** coverage
- Backend tests: **60%+** coverage

```bash
# Run all tests before committing
make test

# Run specific test suites
make test-unit      # Fast unit tests (70%+ coverage required)
make test-backend   # Backend tests (60%+ coverage required)
make test-e2e       # E2E with emulators

# Frontend E2E with Playwright
cd frontend && npm run e2e
```

**New features require tests.** No exceptions.

### Playwright for Frontend Issues

When investigating frontend bugs or verifying behavior:

```bash
cd frontend

# Run e2e tests against production
BASE_URL=https://decide.nomadkaraoke.com npx playwright test

# Debug with visible browser
npx playwright test --headed --debug

# Capture traces for failed tests
npx playwright test --trace on
```

See [docs/TESTING.md](docs/TESTING.md) for Playwright setup and usage.

### Version Bumping

- Bump `tool.poetry.version` in `pyproject.toml` for code changes
- Skip version bump for docs-only changes

### Infrastructure

- All GCP changes via **Pulumi** in `infrastructure/`
- Use `gcloud` CLI only for reading/debugging
- Stop and notify on auth issues

### Music Data Modeling (CRITICAL)

**We have 15M artists and 256M tracks in BigQuery. USE THEM.**

When storing any music-related user data (artists, songs, preferences):

1. **Always use autocomplete** - NEVER allow free-text entry for artists or songs. Users must select from our catalog via autocomplete search.

2. **Store Spotify IDs as primary keys** - Reference `spotify_artists.artist_id` or `spotify_tracks.spotify_id`, not plain text names. This enables:
   - Joining with audio features, genres, popularity data
   - Deduplication (no "Green Day" vs "green day" vs "Greenday" issues)
   - Future features like "similar artists" that require ID-based lookups

3. **Denormalize display names** - Store the ID as the primary reference, but include `artist_name` / `title` as denormalized fields for display. Example:
   ```python
   # CORRECT
   user_artist = {
       "artist_id": "7oPftvlwr6VrsViSDV7fJY",  # Primary key - Spotify ID
       "artist_name": "Green Day",              # Denormalized for display
       "source": "quiz",
   }

   # WRONG - plain text with no ID reference
   user_artist = {
       "artist_name": "Green Day",  # Can't join with Spotify data!
       "source": "quiz",
   }
   ```

4. **API endpoints for user input** - Any endpoint accepting artist/song input should:
   - Accept Spotify IDs (not names) in the request
   - Frontend should use autocomplete components that return IDs
   - See `/api/catalog/artists?q=` and `/api/catalog/songs?q=` for search APIs

5. **Reference the Spotify catalog docs** - See [docs/SPOTIFY-DATA-CATALOG.md](docs/SPOTIFY-DATA-CATALOG.md) for all available tables, schemas, and example queries.

**Why this matters:** Without IDs, we can't leverage our 230M audio features, 2.2M artist-genre mappings, or any cross-referencing. Plain text names are a dead end.

## Code Patterns

### Backend Services

```python
# All services follow this pattern
class SomeService:
    def __init__(self, firestore_service: FirestoreService):
        self.db = firestore_service

    async def do_something(self, ...) -> Result:
        # Business logic here
        pass
```

### API Routes

```python
# Routes use dependency injection
@router.get("/endpoint")
async def get_endpoint(
    user: User = Depends(get_current_user),
    service: SomeService = Depends(get_some_service),
) -> ResponseModel:
    return await service.do_something()
```

### CLI Commands

```python
# CLI uses Click with Rich for output
@click.command()
@click.option("--verbose", "-v", is_flag=True)
def some_command(verbose: bool):
    console = Console()
    # Command logic
```

## Directory Structure

```
karaoke_decide/    # Core Python package (CLI + shared logic)
backend/           # FastAPI API server
frontend/          # Next.js web app (Phase 2)
infrastructure/    # Pulumi IaC
tests/             # Test suites
docs/              # Documentation
scripts/           # Dev utilities
```

## Documentation Maintenance

- **Before PRs:** Run `/docs-review` to check if docs need updating
- **Significant work:** Create `docs/archive/YYYY-MM-DD-topic.md`
- **Status changes:** Update `docs/README.md`
- **Lessons learned:** Add entries to `docs/LESSONS-LEARNED.md`
- **Periodic check:** Run `/docs-maintain` to verify doc organization

## Slash Commands

See `~/.claude/CLAUDE.md` for full workflow. Key commands:

| Command | Purpose |
|---------|---------|
| `/new-worktree <desc>` | Start work in isolated worktree |
| `/plan` | Create implementation plan |
| `/implement` | Implement from plan |
| `/test` | Run tests |
| `/docs-review` | Check docs before PR |
| `/coderabbit` | Run CodeRabbit CLI locally |
| `/pr` | Create PR (adds @coderabbitai ignore) |

## Common Tasks

### Adding a New API Endpoint

1. Create route in `backend/api/routes/`
2. Add models in `backend/models/`
3. Implement service in `backend/services/`
4. Register route in `backend/api/routes/__init__.py`
5. Add tests in `backend/tests/`
6. Update `docs/API.md`

### Adding a New CLI Command

1. Create command in `karaoke_decide/cli/`
2. Register in command group
3. Add tests in `tests/unit/cli/`
4. Update README with usage

### Adding External Service Integration

1. Create client in `karaoke_decide/services/`
2. Add credentials to Secret Manager (via Pulumi)
3. Update config in `karaoke_decide/core/config.py`
4. Document in `docs/ARCHITECTURE.md`
