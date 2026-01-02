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

# Run with Mailslurp (creates temporary test accounts)
MAILSLURP_API_KEY=<key> npx playwright test e2e/production-comprehensive.spec.ts

# Run with pre-authenticated token for service-related tests
# (Use andrew@beveridge.uk account which has Spotify/Last.fm connected)
MAILSLURP_API_KEY=<key> PROD_TEST_TOKEN=<jwt> npx playwright test e2e/production-comprehensive.spec.ts

# Run only authenticated tests (faster, no Mailslurp needed)
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
- Magic link authentication flow (via Mailslurp)
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
