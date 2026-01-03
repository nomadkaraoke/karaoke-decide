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

## Admin User Setup

Admin users have access to the `/admin` dashboard for user management, sync job monitoring, and system stats. Admin status is controlled by the `is_admin` field in Firestore.

### Setting Admin Users

**Option 1: Firebase Console**
1. Go to [Firebase Console](https://console.firebase.google.com) → Your Project → Firestore
2. Navigate to `users` collection
3. Find the user document by ID or email
4. Add or set field: `is_admin` = `true` (boolean)

**Option 2: Firebase CLI**
```bash
# First, find the user ID (from Firebase Console or by querying)
firebase firestore:get users/<USER_ID>

# Update the user document
firebase firestore:set users/<USER_ID> --merge '{"is_admin": true}'
```

**Option 3: Python Script**
```python
from google.cloud import firestore

db = firestore.Client()

# By user ID
db.collection("users").document("<USER_ID>").update({"is_admin": True})

# Or find by email first
users = db.collection("users").where("email", "==", "admin@example.com").get()
for user in users:
    user.reference.update({"is_admin": True})
    print(f"Set admin for user {user.id}")
```

### Verifying Admin Access

After setting `is_admin: true`:
1. Have the user log out and log back in (to refresh their JWT)
2. They should see an "Admin" link in the navigation
3. Navigating to `/admin` should show the dashboard

### Revoking Admin Access

Set `is_admin` to `false` or delete the field:
```python
db.collection("users").document("<USER_ID>").update({"is_admin": False})
```

---

## Adding a New Feature

1. **Plan**: Update PLAN.md if needed
2. **Models**: Add/update models in `karaoke_decide/core/models.py`
3. **Service**: Add business logic in `backend/services/`
4. **Routes**: Add API endpoints in `backend/api/routes/`
5. **CLI**: Add commands in `karaoke_decide/cli/`
6. **Tests**: Add tests in `tests/` and `backend/tests/`
7. **Docs**: Update relevant documentation
