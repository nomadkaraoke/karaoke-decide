# Claude Code Guidelines for Nomad Karaoke Decide

## Quick Reference

| Topic | Location |
|-------|----------|
| Project background | CONTEXT.md |
| Implementation plan | PLAN.md |
| Architecture | docs/ARCHITECTURE.md |
| Development setup | docs/DEVELOPMENT.md |
| API reference | docs/API.md |

## Project Overview

**Nomad Karaoke Decide** helps people discover and choose karaoke songs based on their music listening history.

- **Domain:** decide.nomadkaraoke.com
- **Tech Stack:** Python 3.11+, FastAPI, Firestore, Cloud Run
- **Sister Project:** [karaoke-gen](https://github.com/nomadkaraoke/karaoke-gen) - shares patterns

## Essential Rules

### Git Workflow

1. **Never commit directly to `main`** - always use feature branches
2. **Use git worktrees** for parallel work:
   ```bash
   git worktree add -b feature/xyz ../karaoke-decide-feat-xyz main
   ```
3. **PR format:** Summary, changes list, testing info
4. **Merge only after CI passes**

### Testing (Required Before Commit)

```bash
# Run all tests (should complete in <2 min)
make test

# Run specific test suites
make test-unit      # Fast unit tests
make test-backend   # Backend tests
make test-e2e       # Emulator tests (if applicable)
```

### Version Bumping

- Bump `tool.poetry.version` in `pyproject.toml` for code changes
- Skip version bump for docs-only changes

### Infrastructure

- All GCP changes via **Pulumi** in `infrastructure/`
- Use `gcloud` CLI only for reading/debugging
- Stop and notify on auth issues

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

- **Before PRs:** Check if docs need updating
- **Significant work:** Create `docs/archive/YYYY-MM-DD-topic.md`
- **Status changes:** Update `docs/README.md`

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
