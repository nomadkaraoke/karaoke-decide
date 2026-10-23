# Testing & Code Quality Guide

This document defines testing standards, code quality principles, and CI requirements for Nomad Karaoke Decide.

## Core Principles

### SOLID Principles

All code should follow SOLID principles:

1. **Single Responsibility** - Each class/function does one thing well
2. **Open/Closed** - Open for extension, closed for modification
3. **Liskov Substitution** - Subtypes must be substitutable for base types
4. **Interface Segregation** - Many specific interfaces over one general interface
5. **Dependency Inversion** - Depend on abstractions, not concretions

### Code Quality Standards

- **Maintainability** - Code should be easy to understand and modify
- **Testability** - Code should be designed for easy testing (dependency injection, pure functions)
- **No magic numbers** - Use named constants
- **Explicit over implicit** - Clear is better than clever
- **DRY but not prematurely** - Don't repeat yourself, but don't abstract too early

## Test Types

### Unit Tests (`tests/unit/`)

**Purpose:** Test individual functions/classes in isolation.

**Characteristics:**
- Fast (< 1ms per test)
- No external dependencies (mocked)
- No network, database, or filesystem access
- Run on every commit

**Coverage target:** 70%+ for `karaoke_decide/` package

```python
# Good unit test example
def test_normalize_song_title():
    assert normalize_title("Don't Stop Believin'") == "dont stop believin"
    assert normalize_title("  HELLO  WORLD  ") == "hello world"
```

### Integration Tests (`tests/integration/`)

**Purpose:** Test interactions between components.

**Characteristics:**
- May use real database (emulator)
- Tests service boundaries
- Slower than unit tests

**Example scenarios:**
- API route → Service → Repository chain
- Multiple services working together

### Backend Tests (`backend/tests/`)

**Purpose:** Test API endpoints and backend services.

**Characteristics:**
- Uses FastAPI TestClient
- May mock external services
- Tests request/response contracts

**Coverage target:** 60%+ for `backend/` package

```python
# Good backend test example
def test_search_songs_returns_results(client):
    response = client.get("/api/catalog/songs?q=bohemian")
    assert response.status_code == 200
    data = response.json()
    assert "songs" in data
    assert len(data["songs"]) > 0
```

### E2E Tests (`frontend/e2e/`)

**Purpose:** Test complete user workflows through the real UI.

**Tool:** Playwright

**Characteristics:**
- Runs against real deployed application
- Tests critical user journeys
- Slower, run less frequently

**When to use:**
- Smoke tests after deployment
- Investigating frontend issues
- Verifying critical flows work end-to-end

### Smoke Tests (`frontend/e2e/smoke.spec.ts`)

**Purpose:** Quick verification that production is working.

**Runs:** After deploy to main branch

**Should verify:**
- Homepage loads
- Search returns results
- No console errors
- Critical UI elements present

## Playwright Usage

### Installation

```bash
cd frontend
npm install -D @playwright/test
npx playwright install
```

### Running E2E Tests

```bash
# Run all e2e tests locally against dev server
npm run e2e

# Run against production
BASE_URL=https://decide.nomadkaraoke.com npx playwright test

# Run with UI mode for debugging
npx playwright test --ui

# Run specific test file
npx playwright test e2e/smoke.spec.ts
```

### Investigating Frontend Issues

When debugging frontend issues, use Playwright to:

1. **Reproduce the issue** - Write a test that fails
2. **Debug visually** - Use `--ui` mode or `--headed`
3. **Capture evidence** - Screenshots, traces, console logs
4. **Verify the fix** - Test passes after fix

```bash
# Debug mode with browser visible
npx playwright test --headed --debug

# Generate trace for failed tests
npx playwright test --trace on
```

### Writing E2E Tests

```typescript
// frontend/e2e/smoke.spec.ts
import { test, expect } from '@playwright/test';

test.describe('Smoke Tests', () => {
  test('homepage loads and shows search', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByPlaceholder('Search songs')).toBeVisible();
  });

  test('search returns results', async ({ page }) => {
    await page.goto('/');
    await page.getByPlaceholder('Search songs').fill('bohemian');
    await expect(page.getByText('Bohemian Rhapsody')).toBeVisible({ timeout: 5000 });
  });

  test('no console errors on homepage', async ({ page }) => {
    const errors: string[] = [];
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text());
    });
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    expect(errors).toHaveLength(0);
  });
});
```

## Local stack

The main API (`:8030`) plus the search service (`:8020`) on fixture data, for the mobile app's Maestro E2E flows. The main API uses **gen's local-stack Firestore emulator** (`127.0.0.1:8080`, project `test-project`), so gen sessions minted by gen's local stack (`:8010`) validate in decide. No BigQuery, no GCS.

```bash
# 1. gen first (karaoke-gen); the secret lets search /resolve call gen
DECIDE_GEN_PARTNER_SECRET=local-stack-secret scripts/local-stack.sh up
# 2. decide
make stack-up                       # or scripts/local-stack.sh up   (idempotent)
scripts/local-stack.sh status | down | logs [api|search] | env
```

- `up` builds the fixture index (`.local-stack/song-index/`) and fixture catalog (`.local-stack/catalog/catalog.db`, `python -m karaoke_decide.catalog.fixtures build`), starts search, then `uvicorn backend.main:app` on :8030 with `FIRESTORE_EMULATOR_HOST=127.0.0.1:8080`, `GOOGLE_CLOUD_PROJECT=test-project`, `ENVIRONMENT=development`, `JWT_SECRET=local-stack-jwt`, `DECIDE_TEST_API=1`, `BIGQUERY_DISABLED=1`, `CATALOG_LOCAL_DIR`, `SONG_INDEX_LOCAL_DIR`. It waits for `/api/ready` to report `catalog:true` (60 tries). If nothing listens on :8080 it runs `scripts/start-emulators.sh` itself. State: `.local-stack/{api,search}.{pid,log}`. `down` stops only decide's two servers; the docker emulators (gen's stack) keep running. The stack refuses a non-loopback `GEN_API_URL` and always uses the partner secret `local-stack-secret` (the caller's `DECIDE_GEN_PARTNER_SECRET` is ignored; override with `LOCAL_STACK_PARTNER_SECRET`).
- `env` prints `EXPO_PUBLIC_DECIDE_API_URL` and `EXPO_PUBLIC_DECIDE_SEARCH_URL` for the mobile app (`DECIDE_LOCAL_PUBLIC_URL` / `SEARCH_LOCAL_PUBLIC_URL=http://10.0.2.2:...` for an Android emulator). Ports: `DECIDE_LOCAL_PORT`, `SEARCH_LOCAL_PORT`.
- Smoke test:

  ```bash
  T=$(curl -s -XPOST 127.0.0.1:8010/api/users/auth/google -H 'content-type: application/json' \
      -d '{"id_token":"fake:me@example.com"}' | jq -r .session_token)      # gen session token (no dots)
  curl -H "Authorization: Bearer $T" 127.0.0.1:8030/api/auth/me                   # 200
  curl -XPOST 127.0.0.1:8030/api/test/profile/seed -H 'content-type: application/json' \
      -d '{"email":"me@example.com","artists":[{"name":"ABBA","affinity":"love"}]}'
  curl -H "Authorization: Bearer $T" 127.0.0.1:8030/api/my/recommendations/v2   # sections
  ```

### Local stack: fakes

The local stack's API always runs with **`DECIDE_FAKE_EXTERNALS=1`** and **`DECIDE_INLINE_TASKS=1`**, so no import ever leaves the machine. The app refuses to start with either flag when `ENVIRONMENT=production`.

- **Fakes** (`backend/fakes/`, mounted at `/fake` only with the flag): ListenBrainz (`/fake/listenbrainz/1`: `user/{u}/listen-count`, `user/{u}/listens` with `min_ts`/`max_ts`/`count` like LB, `stats/user/{u}/artists|recordings`, `validate-token`, `POST import-listens/`, `GET import-listens/{id}/` which says `in_progress` on the first check and `completed` from the second), MetaBrainz OAuth (`/fake/metabrainz/oauth2/authorize|token|userinfo`: authorize skips consent and 302s straight back to the callback with `code=fake-<n>`; token checks the PKCE S256 verifier; refresh tokens rotate), the Spotify embed page (`/fake/spotify/embed/playlist/fixtureplaylist0000001`, every other id 404s; ids are 22 characters, so paste `https://open.spotify.com/playlist/fixtureplaylist0000001`), Last.fm (`/fake/lastfm/2.0/?method=user.getinfo|user.gettoptracks|user.gettopartists|user.getlovedtracks|user.getrecenttracks`; no loved tracks, no scrobbles) and Gemini (`FakeScreenshotExtractor`: artist "Muse" 0.95, song "Adele" / "Hello" 0.9, artist "The Invented Band" 0.6, unmatched in the fixture catalog).
- **Env** `scripts/local-stack.sh` exports, all on the API's own port (`DECIDE_LOCAL_PORT`, default 8030; Android reaches it through `adb reverse`): `PUBLIC_API_BASE_URL=http://127.0.0.1:$PORT`, `LISTENBRAINZ_API_BASE=…/fake/listenbrainz/1`, `METABRAINZ_BASE_URL=…/fake/metabrainz`, `SPOTIFY_EMBED_BASE=…/fake/spotify`, `LASTFM_API_BASE=…/fake/lastfm/2.0/`, `LISTENBRAINZ_OAUTH_ENABLED=1`, `METABRAINZ_CLIENT_ID=fake`, `METABRAINZ_CLIENT_SECRET=fake`, and `DECIDE_TOKEN_KEY` set to a **test-only** Fernet key committed in the script. `GEMINI_API_KEY` is unset. So `GET /api/services/capabilities` reports `screenshot_import` and `listenbrainz_oauth` both `true`.
- **Inline tasks:** `CloudTasksService.create_sync_task` POSTs `{PUBLIC_API_BASE_URL}/internal/sync/process` as a background asyncio task instead of creating a Cloud Task (non-production needs no OIDC). It runs as its own request, so the enqueuing request returns at once. There are no retries: a failed run is logged at WARNING.
- **Default state:** `fixture-user` exists on ListenBrainz (5 recent listens) and Last.fm (3 top tracks, 3 top artists), and MetaBrainz OAuth signs in as `fixture-user`. All fake state is in memory: restarting the API drops issued tokens, so reconnect ListenBrainz after a restart.
- **Test API** (with `DECIDE_TEST_API=1`; 409 `fake_externals_disabled` while the fakes are off):
  - `PUT /api/test/fake-externals {"knobs": {...}}`: resets every fake to the defaults, then sets the knobs. Knobs: `metabrainz=deny` (authorize returns `error=access_denied`), `metabrainz_user=<name>`, `spotify_embed=changed` (the import answers 422), `screenshot=fail` (extract answers 503). Unknown knobs are a 422.
  - `POST /api/test/fake-listenbrainz/users {"username", "listens": [{"artist","title","listened_at"}]}`
  - `POST /api/test/fake-lastfm/users {"username", "top_tracks": [{"artist","title","playcount"}], "top_artists": [{"name","playcount"}]}`
  - `POST /api/test/listenbrainz/poll` runs the `lb-poll` job's poller once (listens and pending imports).
- Running a second stack next to another session's: `DECIDE_LOCAL_PORT=8031 SEARCH_LOCAL_PORT=8022 scripts/local-stack.sh up`. Avoid port 8021 on macOS: launchd holds it for the ftp-proxy service.
- Tests: `backend/tests/test_fakes.py` runs the app in a real uvicorn server pointed at its own `/fake` routes.

### Playwright against the local stacks

Drive the web app in a real browser against gen's local stack (:8010) and decide's (:8030/:8020), with profiles seeded via `/api/test/profile/seed`.

- Run the dev server on **`localhost:3000`** (`cd frontend && npm run dev`): the local APIs only allow that origin in CORS, so another port or `127.0.0.1` fails with CORS errors.
- Use locale-prefixed paths (`http://localhost:3000/en/recommendations`); bare paths redirect and lose the dev context.
- Sign in with gen's local email code (gen's stack returns/logs the code) or mint a session via gen's `/api/users/auth/google` fake id token (see smoke test above) and put it in `localStorage`.
- **Don't run `npm run build` while `next dev` is running**: both write `.next/` and the dev server breaks (500s, missing chunks). Stop dev first, or restart it after the build.

## Search local stack

`scripts/search-local-stack.sh` is a thin wrapper for `scripts/local-stack.sh <cmd> --search-only`: just the search service on :8020 (no Firestore, no catalog, gen not required except for `/resolve`). Everything below applies to it and to the full stack's search half.

```bash
make search-stack-up            # or scripts/search-local-stack.sh up   (idempotent)
scripts/search-local-stack.sh status
scripts/search-local-stack.sh down
curl -s 'http://127.0.0.1:8020/api/search?q=starlite%20muse' | jq .identification.status   # "confident"
```

- `up` builds the fixture index into `.local-stack/song-index/` (`python -m karaoke_decide.song_search.fixtures build <dir>`, which reuses the song-index-build job's local dry run on `tests/fixtures/song_search/`), then runs `uvicorn backend.search.app:search_app` on :8020 with `SONG_INDEX_LOCAL_DIR`, `GEN_API_URL` (default `http://127.0.0.1:8010`) and `DECIDE_GEN_PARTNER_SECRET=local-stack-secret`. The pid is `.local-stack/search.pid`, the log `.local-stack/search.log`.
- `GET /api/search/offline-index` returns a URL on the same server (`/api/search/_local/offline.db.gz`), a route mounted only when `SONG_INDEX_LOCAL_DIR` is set. For an Android emulator set `SEARCH_LOCAL_PUBLIC_URL=http://10.0.2.2:8020` before `up` so the manifest URL is reachable from the device; `SEARCH_LOCAL_PORT` changes the port.
- **`/api/search/resolve`** calls gen's `POST /api/kjbox/catalog/resolve`, which needs both stacks to share the partner secret. Start gen's local stack with it (gen reads `DECIDE_GEN_PARTNER_SECRET` from the environment, and `local-stack.sh` passes its environment through to the backend):

  ```bash
  DECIDE_GEN_PARTNER_SECRET=local-stack-secret scripts/local-stack.sh up   # in karaoke-gen
  ```

  Without it gen rejects the call and `/resolve` answers `{}` (same as when decide has no secret).

## Catalog fixture

Recommendations tests run on a deterministic fixture `catalog.db` (invented facets, built from `tests/fixtures/song_search/kn_full_fixture.json` plus synthetic artists): `python -m karaoke_decide.catalog.fixtures build <dir>` writes `<dir>/catalog.db`, and backend tests pair it with the search fixture index (`availability.db`, the `index_dir` fixture in `backend/tests/search/conftest.py`). Set `CATALOG_LOCAL_DIR` / `SONG_INDEX_LOCAL_DIR` to load already-built files. Builder tests use the factories in `tests/unit/catalog/conftest.py`. For a real size check run `python -m karaoke_decide.etl.musicbrainz_refresh catalog --no-publish --workdir <dir>` (real BigQuery, read-only ADC is enough; nothing is uploaded).

## CI Pipeline

### Required Checks (Block PR Merge)

All PRs must pass:

| Check | Description | Threshold |
|-------|-------------|-----------|
| `lint` | Ruff + mypy | No errors |
| `test-unit` | Unit tests | 70% coverage |
| `test-backend` | Backend tests | 60% coverage |
| `test-frontend` | Build + type check | Must pass |

### Post-Merge Checks

After merging to main:

| Check | Description |
|-------|-------------|
| `e2e-smoke` | Production smoke tests |
| `deploy-frontend` | Deploy to GitHub Pages |

### Known Testing Gaps

**Infrastructure dependencies** (Cloud Tasks, BigQuery, Firestore) are mocked in tests. This means IAM permission errors won't be caught until production. Mitigations:

1. **Authenticated smoke tests** - Use `e2e/sync-integration.spec.ts` with a test account token
2. **Manual verification** after infrastructure changes (Pulumi updates)
3. **Deep health endpoint** - `/api/health/deep` validates connectivity to all infrastructure

### Production E2E Testing

For comprehensive production testing with automated email login:

```bash
cd frontend

# Run with TestMail.app (uses unique tag-based email addresses)
TESTMAIL_API_KEY=<key> TESTMAIL_NAMESPACE=<ns> npx playwright test e2e/production-comprehensive.spec.ts

# Run with pre-authenticated token for service-related tests
# (Use andrew@beveridge.uk account which has Spotify/Last.fm connected)
TESTMAIL_API_KEY=<key> TESTMAIL_NAMESPACE=<ns> PROD_TEST_TOKEN=<jwt> npx playwright test e2e/production-comprehensive.spec.ts

# Run only authenticated tests (faster, no TestMail needed)
PROD_TEST_TOKEN=<jwt> npx playwright test e2e/production-comprehensive.spec.ts --grep "Authenticated"
```

#### Generating PROD_TEST_TOKEN

**Option 1: Use the automated script (recommended for CI/agents)**

```bash
# Install dependencies (if not already installed)
pip install google-cloud-firestore google-cloud-secret-manager python-jose

# Generate token for default test user (andrew@beveridge.uk)
python scripts/get_prod_test_token.py

# Generate token for a specific user
python scripts/get_prod_test_token.py --email user@example.com

# Use directly with tests
PROD_TEST_TOKEN=$(python scripts/get_prod_test_token.py) npx playwright test e2e/production-comprehensive.spec.ts --grep "Authenticated"
```

Prerequisites for the script:
- `gcloud auth application-default login` (authenticated with access to nomadkaraoke project)
- Access to Secret Manager secrets (JWT_SECRET)

**Option 2: Manual extraction from browser**

1. Login to https://decide.nomadkaraoke.com
2. Open browser DevTools → Application → Local Storage
3. Copy the `karaoke_decide_token` value

The comprehensive tests cover:
- Magic link authentication flow (via TestMail.app)
- Search and catalog browsing
- Services page and sync functionality
- My Songs, Recommendations, Playlists pages
- Quiz functionality
- **Known Songs page** (search, add songs)
- **My Data page** (preferences)
- API health endpoints
- Error handling

## Running Tests Locally

```bash
# All tests
make test

# Specific suites
make test-unit       # Unit tests only
make test-backend    # Backend tests only
make test-e2e        # E2E with emulators

# With coverage
poetry run pytest tests/unit --cov=karaoke_decide --cov-report=html
open htmlcov/index.html

# Frontend e2e
cd frontend && npm run e2e
```

## Test File Organization

```
karaoke-decide/
├── tests/
│   ├── unit/                    # Unit tests for karaoke_decide package
│   │   ├── test_models.py
│   │   └── test_text_utils.py
│   ├── integration/             # Integration tests
│   └── conftest.py              # Shared fixtures
├── backend/
│   └── tests/
│       ├── test_health.py       # API endpoint tests
│       ├── test_catalog.py
│       └── emulator/            # Tests requiring GCP emulators
└── frontend/
    ├── e2e/                     # Playwright E2E tests
    │   ├── smoke.spec.ts        # Production smoke tests
    │   └── search.spec.ts       # Search flow tests
    └── playwright.config.ts
```

## Writing Good Tests

### Do

- Test behavior, not implementation
- Use descriptive test names: `test_search_returns_empty_list_when_no_matches`
- One assertion per test (when practical)
- Use fixtures for common setup
- Test edge cases and error conditions

### Don't

- Test private methods directly
- Write tests that depend on execution order
- Use sleep/wait without timeouts
- Test framework code (FastAPI, React)
- Skip tests without a reason

## Coverage Enforcement

Coverage is enforced in CI:

```yaml
# Unit tests require 70% coverage
poetry run pytest tests/unit --cov-fail-under=70

# Backend tests require 60% coverage
poetry run pytest backend/tests --cov-fail-under=60
```

To check coverage locally:

```bash
poetry run pytest tests/unit --cov=karaoke_decide --cov-report=term-missing
```

## Mocking Guidelines

### When to Mock

- External APIs (Spotify, Last.fm)
- Database calls in unit tests
- Time-dependent operations
- Network requests

### When NOT to Mock

- Your own code (test the real thing)
- Simple data transformations
- In integration tests (use emulators)

### Mock Example

```python
from unittest.mock import Mock, patch

def test_fetch_spotify_tracks_handles_rate_limit():
    with patch('karaoke_decide.services.spotify.httpx.get') as mock_get:
        mock_get.return_value = Mock(status_code=429)

        with pytest.raises(RateLimitError):
            fetch_spotify_tracks("query")
```

## Debugging Failed Tests

### Local Debugging

```bash
# Run single test with verbose output
poetry run pytest tests/unit/test_models.py::test_song_validation -vvs

# Drop into debugger on failure
poetry run pytest --pdb

# Show local variables on failure
poetry run pytest -l
```

### CI Debugging

1. Check the GitHub Actions logs
2. Look for the specific assertion that failed
3. Reproduce locally with the same command
4. If flaky, check for timing/ordering issues

## Adding Tests for New Features

When adding a new feature:

1. **Write tests first** (TDD) or immediately after
2. **Unit tests** for business logic
3. **Backend tests** for new API endpoints
4. **E2E tests** for critical user flows
5. **Update this doc** if new patterns emerge
