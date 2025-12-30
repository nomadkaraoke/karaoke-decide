# Development Guide

## Prerequisites

- Python 3.11+
- Poetry
- Docker (for emulators)
- Google Cloud SDK (optional, for deployment)

## Initial Setup

```bash
# Clone the repository
git clone https://github.com/nomadkaraoke/karaoke-decide
cd karaoke-decide

# Install dependencies
poetry install

# Copy environment template
cp .env.example .env
# Edit .env with your credentials
```

## Running Locally

### API Server

```bash
# Start the development server
make dev

# Or manually
poetry run uvicorn backend.main:app --reload --port 8000
```

The API will be available at http://localhost:8000

- Swagger docs: http://localhost:8000/api/docs
- ReDoc: http://localhost:8000/api/redoc

### CLI

```bash
# Run CLI commands
make cli ARGS="--help"

# Or directly
poetry run karaoke-decide --help
poetry run karaoke-decide songs search "bohemian"
```

## Testing

```bash
# Run all tests
make test

# Run only unit tests
make test-unit

# Run backend tests
make test-backend

# Run tests with emulators (e2e)
make test-e2e

# Run specific test file
poetry run pytest tests/unit/test_text_utils.py -v
```

## GCP Emulators

For local development without GCP:

```bash
# Start Firestore and Storage emulators
make emulators

# In another terminal, run the app with emulator settings
FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 make dev

# Stop emulators when done
make stop-emulators
```

## Code Quality

```bash
# Run linters
make lint

# Format code
make format
```

## Project Structure

```
karaoke-decide/
├── karaoke_decide/       # Core Python package
│   ├── cli/              # CLI commands
│   ├── core/             # Models, config, exceptions
│   ├── services/         # External API clients
│   └── utils/            # Utilities
├── backend/              # FastAPI application
│   ├── api/routes/       # API endpoints
│   ├── services/         # Business logic
│   └── tests/            # Backend tests
├── tests/                # Package tests
│   ├── unit/
│   └── integration/
├── docs/                 # Documentation
├── infrastructure/       # Pulumi IaC
└── scripts/              # Dev utilities
```

## Environment Variables

See `.env.example` for all available variables. Key ones:

| Variable | Description | Required |
|----------|-------------|----------|
| `GOOGLE_CLOUD_PROJECT` | GCP project ID | Yes (prod) |
| `SPOTIFY_CLIENT_ID` | Spotify app client ID | Yes |
| `SPOTIFY_CLIENT_SECRET` | Spotify app client secret | Yes |
| `LASTFM_API_KEY` | Last.fm API key | Yes |
| `JWT_SECRET` | Secret for signing tokens | Yes |
| `SENDGRID_API_KEY` | For magic link emails | Yes |

## Adding a New Feature

1. **Plan**: Update PLAN.md if needed
2. **Models**: Add/update models in `karaoke_decide/core/models.py`
3. **Service**: Add business logic in `backend/services/`
4. **Routes**: Add API endpoints in `backend/api/routes/`
5. **CLI**: Add commands in `karaoke_decide/cli/`
6. **Tests**: Add tests in `tests/` and `backend/tests/`
7. **Docs**: Update relevant documentation
