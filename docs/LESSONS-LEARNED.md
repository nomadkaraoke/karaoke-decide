# Lessons Learned

Accumulated wisdom from building Nomad Karaoke Decide. Add entries as you learn things that future sessions should know.

## Format

```markdown
### YYYY-MM-DD: Brief Title
**Context:** What you were doing
**Lesson:** What you learned
**Recommendation:** What to do differently
```

---

## Entries

### 2026-01-02: Mock google.cloud Before Imports in conftest.py

**Context:** Adding new API routes that triggered imports of modules using `google.cloud.tasks_v2`, which isn't available in the test environment.

**Lesson:** When using `patch()` for dependency injection in pytest, the modules being patched must be imported before the patch decorators are evaluated. But importing modules that depend on unavailable packages (like `google.cloud.tasks_v2`) will fail.

**Recommendation:** Mock unavailable packages at module level in conftest.py using `sys.modules` before any imports:
```python
import sys
from unittest.mock import MagicMock

# Mock before any imports that need it
_mock_tasks_v2 = MagicMock()
_mock_tasks_v2.HttpMethod.POST = 1
sys.modules["google.cloud.tasks_v2"] = _mock_tasks_v2

# Now safe to import modules that depend on it
import backend.api.deps  # noqa: E402
```

---

### 2024-12-30: BigQuery over Firestore for Large Catalogs

**Context:** Loading 275K karaoke songs and 256M Spotify tracks for search/browse.

**Lesson:** BigQuery is better than Firestore for large read-heavy datasets with complex queries. Firestore would require denormalized indexes and be expensive for full-text search across millions of records.

**Recommendation:** Use BigQuery for catalog data (songs, tracks). Reserve Firestore for user-specific data (preferences, playlists, history) where document-based access patterns make sense.

---

### 2024-12-30: GCE VM for Large ETL Jobs

**Context:** Needed to process 186GB Spotify torrent data (decompress .zst, query SQLite, export to BigQuery).

**Lesson:** Local machines with SD cards are too slow for large ETL. Spinning up a GCE VM with SSD in the same region as BigQuery is much faster (minutes vs hours).

**Recommendation:** For large data processing:
1. Create GCE VM near your data destination
2. Download/process data there
3. Delete VM when done to avoid costs

---

### 2024-12-30: Cloud Run Port Configuration

**Context:** Deploying FastAPI to Cloud Run, got "container failed to start" errors.

**Lesson:** Cloud Run defaults to PORT=8080. If your Dockerfile uses a different port (we used 8000), you must specify `--port=8000` in the deploy command.

**Recommendation:** Either:
- Update Dockerfile to use 8080
- Or always specify `--port` in `gcloud run deploy`

---

### 2024-12-30: Service Account Permissions for BigQuery

**Context:** Cloud Run service couldn't query BigQuery - got 403 "User does not have bigquery.jobs.create permission".

**Lesson:** Default compute service account needs explicit BigQuery roles.

**Recommendation:** Grant these roles to the Cloud Run service account:
```bash
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/bigquery.user"

gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/bigquery.dataViewer"
```

---

### 2024-12-30: GitHub Pages Custom Domain Setup

**Context:** Deploying Next.js static site to decide.nomadkaraoke.com via GitHub Pages.

**Lesson:** GitHub Pages needs to be enabled AND configured for GitHub Actions deployment before the workflow will succeed.

**Recommendation:**
1. Enable Pages in repo settings OR via API: `gh api repos/OWNER/REPO/pages -X POST -f build_type="workflow"`
2. Set custom domain: `gh api repos/OWNER/REPO/pages -X PUT -f cname="your.domain.com"`
3. Create `public/CNAME` file with domain
4. Configure DNS CNAME to point to `username.github.io`

---

### 2024-12-30: Importing Existing GCP Resources into Pulumi

**Context:** Infrastructure was created via `gcloud` commands but CLAUDE.md requires all GCP changes via Pulumi. Needed to bring existing resources under IaC control.

**Lesson:** Pulumi import workflow is straightforward but requires matching IaC to exact API state to avoid drift.

**Recommendation:**
1. Write IaC definitions first (approximate is fine)
2. Import each resource: `pulumi import <type> <name> <gcp-id>`
3. Copy the suggested code from import output to match exact state
4. Run `pulumi preview` - iterate until "0 changes"
5. Use `protect=True` on stateful resources (BigQuery tables, buckets)

**Import ID formats:**
```
BigQuery Dataset: projects/{project}/datasets/{dataset}
BigQuery Table:   projects/{project}/datasets/{dataset}/tables/{table}
GCS Bucket:       {bucket-name}
Artifact Reg:     projects/{project}/locations/{region}/repositories/{repo}
Cloud Run:        projects/{project}/locations/{region}/services/{service}
IAM Member:       {project} {role} {member}
```

---

### 2024-12-30: Lazy Service Initialization for CI

**Context:** Backend tests were failing in CI because `BigQueryCatalogService` was instantiated at module import, requiring GCP credentials.

**Lesson:** Services that require external credentials should use lazy initialization, not module-level instantiation. This allows tests to mock the service before first use.

**Recommendation:**
```python
# BAD - instantiated at import
catalog_service = BigQueryCatalogService()

# GOOD - lazy initialization
_catalog_service: BigQueryCatalogService | None = None

def get_catalog_service() -> BigQueryCatalogService:
    global _catalog_service
    if _catalog_service is None:
        _catalog_service = BigQueryCatalogService()
    return _catalog_service
```

---

### 2024-12-30: Type Annotations for httpx response.json()

**Context:** Mypy was failing with "Returning Any from function declared to return dict[str, Any]" on all `response.json()` calls.

**Lesson:** `response.json()` returns `Any` type. Mypy requires explicit type annotation to satisfy declared return types.

**Recommendation:**
```python
# BAD - mypy error
return response.json()

# GOOD - explicit annotation
result: dict[str, Any] = response.json()
return result
```

---

### 2024-12-30: Mock Sync vs Async Methods Correctly

**Context:** Tests were failing with "coroutine object is not iterable" when mocking a sync method with AsyncMock.

**Lesson:** When mocking a synchronous method that returns a list, use `MagicMock.return_value`, not `AsyncMock(return_value=...)`. AsyncMock wraps the return value in a coroutine, which breaks when the code doesn't await it.

**Recommendation:**
```python
# BAD - if search_songs is synchronous
mock.search_songs = AsyncMock(return_value=[result])

# GOOD - use regular mock for sync methods
mock.search_songs.return_value = [result]
```

---

### 2024-12-30: FastAPI Dependency Defaults

**Context:** Mypy errors on FastAPI route with `music_service: MusicServiceServiceDep = ...` as default.

**Lesson:** Using `...` (Ellipsis) as a default for FastAPI `Depends` parameters causes mypy errors. Instead, put parameters with Depends annotations before Query parameters (order them by required-ness).

**Recommendation:**
```python
# BAD - ellipsis as default
async def callback(
    code: str | None = Query(None),
    music_service: MusicServiceServiceDep = ...,  # mypy error
):

# GOOD - reorder parameters
async def callback(
    music_service: MusicServiceServiceDep,  # Depends params first
    code: str | None = Query(None),
):
```

---

### 2024-12-30: Mypy "Returning Any" with min/max Functions

**Context:** Implementing recommendation scoring function that returns `float`, using `min(score, 1.0)` to cap the score.

**Lesson:** `min()` and `max()` return `Any` when their arguments could be multiple types. Mypy complains "Returning Any from function declared to return float".

**Recommendation:**
```python
# BAD - mypy error
def calculate_score(...) -> float:
    score = ...
    return min(score, 1.0)  # Returns Any

# GOOD - explicit type annotation
def calculate_score(...) -> float:
    score = ...
    capped_score: float = min(score, 1.0)
    return capped_score
```

---

### 2024-12-30: Test Mocks Must Match Service Behavior

**Context:** Testing recommendation service limit parameter. Test was failing because mock returned all 5 songs regardless of requested limit.

**Lesson:** When testing service methods that accept parameters like `limit`, the mock must be configured to return appropriate data for the test case. Don't rely on fixture defaults that may exceed your test's expected output.

**Recommendation:**
```python
# BAD - using default mock that returns 5 songs
async def test_respects_limit(self, recommendation_service, mock_bigquery):
    # mock_bigquery fixture returns 5 songs
    recs = await recommendation_service.get_recommendations(limit=3)
    assert len(recs) <= 3  # May fail if service returns all 5

# GOOD - configure mock specifically for test
async def test_respects_limit(self, recommendation_service, mock_bigquery):
    # Override mock to return only 3 songs
    mock_rows = [create_row(i) for i in range(3)]
    mock_bigquery.query.return_value.result.return_value = mock_rows
    recs = await recommendation_service.get_recommendations(limit=3)
    assert len(recs) <= 3  # Now correctly tests the limit
```

---

### 2024-12-30: FirestoreService order_direction Must Be Uppercase

**Context:** Implementing user songs query with descending play count ordering.

**Lesson:** `FirestoreService.query_documents` only recognizes `"DESCENDING"` (uppercase). Using `"desc"` or `"descending"` silently defaults to ascending order.

**Recommendation:**
```python
# BAD - silently uses ascending order
docs = await firestore.query_documents(
    collection,
    order_by="play_count",
    order_direction="desc",  # Ignored! Defaults to ascending
)

# GOOD - explicitly uppercase
docs = await firestore.query_documents(
    collection,
    order_by="play_count",
    order_direction="DESCENDING",  # Works correctly
)
```

---

### 2024-12-30: Python 3.12 Deprecates datetime.utcnow()

**Context:** Using `datetime.utcnow()` as default factory for Pydantic model fields.

**Lesson:** Python 3.12 deprecates `datetime.utcnow()` in favor of timezone-aware `datetime.now(UTC)`. While it still works, it will show deprecation warnings and should be migrated.

**Recommendation:**
```python
from datetime import UTC, datetime

# BAD - deprecated in Python 3.12
created_at: datetime = Field(default_factory=datetime.utcnow)

# GOOD - timezone-aware
created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

---

### 2025-12-30: CodeRabbit Review - Type Centralization

**Context:** CodeRabbit flagged duplicate TypeScript interfaces defined locally in multiple components instead of using the central types file.

**Lesson:** Keep TypeScript types in a single `src/types/index.ts` file and import them. Duplicate interfaces across components leads to type drift and maintenance burden.

**Recommendation:**
```typescript
// BAD - local interface duplicated in each component
interface Recommendation {
  song_id: string;
  // ... fields
}

// GOOD - import from central types
import type { Recommendation } from "@/types";
```

---

### 2025-12-30: CodeRabbit Review - Next.js SPA Navigation

**Context:** CodeRabbit flagged use of `window.location.href` for navigation in Next.js pages.

**Lesson:** Using `window.location.href` causes full page reloads, losing Next.js client-side routing benefits. Always use `useRouter` from `next/navigation` for internal navigation.

**Recommendation:**
```typescript
// BAD - full page reload
onClick: () => (window.location.href = "/services")

// GOOD - SPA navigation
import { useRouter } from "next/navigation";
const router = useRouter();
onClick: () => router.push("/services")
```

---

### 2025-12-30: CodeRabbit Review - window.open Security

**Context:** CodeRabbit flagged `window.open()` calls missing security parameters when opening external URLs.

**Lesson:** When opening external URLs (like YouTube search), always pass `'noopener,noreferrer'` as the third parameter to prevent the opened page from accessing `window.opener` and to avoid leaking referrer information.

**Recommendation:**
```typescript
// BAD - security risk
window.open(url, "_blank");

// GOOD - secure
window.open(url, "_blank", "noopener,noreferrer");
```

---

### 2025-12-30: React setState During Render Causes Infinite Loops

**Context:** Profile page was syncing user display name to local state directly in the render function body.

**Lesson:** Calling `setState` during render (outside useEffect) causes React to re-render, which triggers setState again, creating an infinite loop. CodeRabbit flagged this as a critical issue.

**Recommendation:**
```typescript
// BAD - setState during render causes infinite loop
function ProfilePage() {
  const [displayName, setDisplayName] = useState("");
  const { user } = useAuth();

  // This runs on EVERY render and triggers another render!
  if (user?.display_name && displayName === "") {
    setDisplayName(user.display_name);
  }
}

// GOOD - use useEffect for side effects
function ProfilePage() {
  const [displayName, setDisplayName] = useState("");
  const { user } = useAuth();

  useEffect(() => {
    if (user?.display_name) {
      setDisplayName(user.display_name);
    }
  }, [user?.display_name]);
}
```

---

### 2025-12-30: Remove Unused Code During Development

**Context:** CodeRabbit flagged unused dataclass and constant in playlist_service.py that were likely scaffolded but never used.

**Lesson:** Unused code (dead code) creates confusion for future developers and AI agents trying to understand the codebase. Remove it immediately rather than leaving it "for later."

**Recommendation:**
- Delete unused classes, functions, constants, and imports
- Don't scaffold code "for future use" - add it when needed
- Run linters that detect unused code (`ruff` flags unused imports)

---

### 2025-12-30: Dropdown Z-Index in Card Lists

**Context:** "Sing it!" dropdown on song cards was appearing beneath subsequent cards in the list, making it impossible to interact with.

**Lesson:** In a list of cards where each card has `position: relative`, subsequent sibling cards create new stacking contexts that can cover earlier cards' dropdowns - even if the dropdown has `z-index: 50`. The parent card itself needs elevated z-index when its dropdown is open.

**Recommendation:**
```tsx
// BAD - dropdown has z-50 but parent card has no z-index
<div className="relative">
  <div className="absolute z-50">Dropdown</div>
</div>

// GOOD - elevate parent card when dropdown is open
<div className={`relative ${isDropdownOpen ? "z-10" : ""}`}>
  <div className="absolute z-50">Dropdown</div>
</div>
```

---

### 2025-12-31: Case-Sensitivity in SQL Batch Matching

**Context:** BigQuery batch track matching was failing to find matches even when songs existed in the catalog.

**Lesson:** When building SQL `WHERE` clauses with `LOWER(column) = 'value'`, the value must also be lowercased in the application code before interpolation. The catalog uses `LOWER(Artist)` but input values weren't being lowercased.

**Recommendation:**
```python
# BAD - case mismatch
safe_artist = artist.replace("'", "''")  # "Queen" won't match LOWER(Artist)='queen'

# GOOD - lowercase to match SQL LOWER()
safe_artist = artist.replace("'", "''").lower()  # "queen" matches LOWER(Artist)='queen'
```

---

### 2025-12-31: Thread-Safe Singleton with Double-Checked Locking

**Context:** Service singletons were being initialized without thread safety, which could cause race conditions in multi-threaded environments.

**Lesson:** Use double-checked locking pattern for thread-safe lazy initialization of singletons. Check twice - once without lock (fast path) and once with lock (safe path).

**Recommendation:**
```python
import threading

_service: MyService | None = None
_service_lock = threading.Lock()

def get_service() -> MyService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:  # Double-check after acquiring lock
                _service = MyService()
    return _service
```

---

### 2025-12-31: React useCallback Declaration Order

**Context:** TypeScript build failing with "Block-scoped variable used before its declaration" when one useCallback referenced another.

**Lesson:** In React/TypeScript, `useCallback` hooks must be declared in dependency order. If `loadServices` depends on `pollSyncStatus` in its dependency array, `pollSyncStatus` must be defined first.

**Recommendation:**
```typescript
// BAD - pollSyncStatus used before declaration
const loadServices = useCallback(() => {
  pollSyncStatus(); // Error: used before declaration
}, [pollSyncStatus]);

const pollSyncStatus = useCallback(() => {...}, []);

// GOOD - define dependencies first
const pollSyncStatus = useCallback(() => {...}, []);

const loadServices = useCallback(() => {
  pollSyncStatus(); // OK - already declared
}, [pollSyncStatus]);
```

---

### 2025-12-31: CI Environment Variables Must Match Infrastructure

**Context:** Magic link auth broke in production after deployment because CI workflow had wrong GOOGLE_CLOUD_PROJECT value.

**Lesson:** When infrastructure (Pulumi) sets environment variables, CI deploy scripts must use identical values. A typo (`nomadkaraoke-decide` vs `nomadkaraoke`) caused Firestore to fail finding users.

**Recommendation:**
- Document required env vars in infrastructure code comments
- Use variables/secrets rather than hardcoding values in multiple places
- After fixing infra, check ALL places that set the same env var (Pulumi, CI, local .env)

---

### 2025-12-31: Python Async Generator Type Annotations

**Context:** Mypy failing with "Function is missing a return type annotation" on an async generator function in tests.

**Lesson:** Async generator functions need explicit `AsyncGenerator[YieldType, SendType]` return type annotation. The `yield` statement alone doesn't satisfy mypy.

**Recommendation:**
```python
from collections.abc import AsyncGenerator
from typing import Any

# BAD - missing type annotation
async def async_empty_generator():
    return
    yield

# GOOD - explicit return type
async def async_empty_generator() -> AsyncGenerator[Any, None]:
    return
    yield
```

---

### 2025-12-31: Cloud Run Secrets Configuration

**Context:** Magic link auth failing in production with "JWT_SECRET is not configured" error.

**Lesson:** Cloud Run env vars set via CI don't persist across deployments when using `--set-env-vars` alone. Secrets need both:
1. IAM binding for Cloud Run SA to access Secret Manager secrets
2. `--set-secrets` flag in deploy command to mount secrets as env vars

**Recommendation:**
```yaml
# In CI workflow deploy step:
gcloud run deploy SERVICE_NAME \
  --set-env-vars "KEY=value" \
  --set-secrets "JWT_SECRET=secret-name:latest"

# IAM binding (via gcloud or Pulumi):
gcloud secrets add-iam-policy-binding SECRET_NAME \
  --member="serviceAccount:SA@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

---

### 2025-12-31: Never Hardcode API Keys in Test Files

**Context:** E2E test file committed with hardcoded Mailslurp API key.

**Lesson:** API keys in source code are a security risk and can be scraped by bots. Always use environment variables for secrets, even in test files.

**Recommendation:**
```typescript
// BAD - hardcoded API key
const API_KEY = "sk_actual_key_value";

// GOOD - read from environment
const API_KEY = process.env.MAILSLURP_API_KEY || "";
```

---

### 2025-12-31: Verify New Settings Are Passed to Cloud Run

**Context:** Cloud Tasks sync failing with "GOOGLE_CLOUD_PROJECT_NUMBER must be set in production" even though the setting was defined in the `Settings` class.

**Lesson:** When adding a new setting to the `Settings` class that's required at runtime, you must also add it to the Cloud Run environment variables in Pulumi. Defining the setting doesn't automatically make it available - it needs to be explicitly passed.

**Recommendation:**
```python
# In karaoke_decide/core/config.py - defining the setting
class Settings(BaseSettings):
    google_cloud_project_number: str = ""  # Step 1: Define setting

# In infrastructure/__main__.py - MUST ALSO add to Cloud Run envs
cloud_run_service = gcp.cloudrunv2.Service(
    template={
        "containers": [{
            "envs": [
                # Step 2: Pass to Cloud Run (easy to forget!)
                {"name": "GOOGLE_CLOUD_PROJECT_NUMBER", "value": PROJECT_NUMBER},
            ],
        }],
    },
)
```

---

### 2025-12-31: Pulumi and CI Deployments Must Stay in Sync

**Context:** Magic link emails weren't being sent. Investigation revealed `SENDGRID_API_KEY` was empty, causing "dev mode" fallback.

**Lesson:** When both Pulumi and CI deploy to Cloud Run, they can overwrite each other's configuration. CI was setting secrets via `--set-secrets`, but running `pulumi up` created a new revision that didn't include those secrets. The two deployment systems must define the same configuration.

**Recommendation:**
```python
# In infrastructure/__main__.py - define ALL env vars and secrets
# Don't rely on CI to set secrets separately
cloud_run_service = gcp.cloudrunv2.Service(
    template={
        "containers": [{
            "envs": [
                # Plain env vars
                {"name": "ENVIRONMENT", "value": environment},
                {"name": "FRONTEND_URL", "value": "https://decide.nomadkaraoke.com"},
                # Secrets - mount from Secret Manager
                {
                    "name": "SENDGRID_API_KEY",
                    "value_source": {
                        "secret_key_ref": {
                            "secret": "sendgrid-api-key",
                            "version": "latest",
                        }
                    },
                },
            ],
        }],
    },
)
```

**Key insight:** If you use Pulumi for infrastructure, put ALL Cloud Run configuration in Pulumi - don't split between Pulumi (env vars) and CI (secrets).

---

### 2025-12-31: Cloud Tasks OIDC Requires iam.serviceAccounts.actAs

**Context:** Sync button returned 403 error in production: "The principal lacks IAM permission iam.serviceAccounts.actAs for the resource".

**Lesson:** When Cloud Tasks creates tasks with OIDC authentication (to call Cloud Run), the service account creating the task needs `roles/iam.serviceAccountUser` on the target service account. This grants the `iam.serviceAccounts.actAs` permission required for OIDC token impersonation.

**Recommendation:**
```python
# In Pulumi infrastructure code
gcp.serviceaccount.IAMMember(
    "compute-sa-act-as-self",
    service_account_id=f"projects/{project}/serviceAccounts/{SA_EMAIL}",
    role="roles/iam.serviceAccountUser",
    member=f"serviceAccount:{SA_EMAIL}",  # Allow SA to act as itself
)
```

**Why tests didn't catch this:** All tests mocked the Cloud Tasks service. No test actually called `CloudTasksClient.create_task()` with real credentials. See "Testing Strategy for Infrastructure" entry below.

---

### 2025-12-31: Testing Strategy for Infrastructure Dependencies

**Context:** IAM permission error wasn't caught because Cloud Tasks was completely mocked in tests.

**Lesson:** Tests that mock external services don't validate IAM permissions or infrastructure configuration. Production-like integration tests are needed for infrastructure-dependent code paths.

**Gap identified:**
- Backend tests mock `CloudTasksService` entirely
- E2E tests mock API responses, never trigger real sync
- Production API tests only test unauthenticated endpoints

**Recommendations:**

1. **Add authenticated production smoke tests** - Run after deploy with a test account:
   ```bash
   PROD_TEST_TOKEN=<jwt> npx playwright test e2e/sync-integration.spec.ts
   ```

2. **Use Cloud Tasks emulator in CI** for integration tests (when available)

3. **Add infrastructure health endpoint** that validates connectivity:
   ```python
   @router.get("/health/deep")
   async def deep_health_check():
       # Test Cloud Tasks queue exists and is accessible
       # Test BigQuery connectivity
       # Test Firestore connectivity
       return {"status": "healthy", "checks": {...}}
   ```

4. **Document required IAM bindings** in infrastructure code with comments explaining why each is needed

---

### 2025-12-31: FirestoreService Uses AsyncClient - Don't Mix Sync/Async

**Context:** Sync status wasn't persisting on page reload. `get_sync_status` was using `list(query.stream())` to get results.

**Lesson:** `FirestoreService` uses `google.cloud.firestore.AsyncClient`, which means `stream()` returns an async iterator. Calling sync `list()` on an async iterator doesn't work correctly - it returns empty results without error.

**Recommendation:**
```python
# BAD - sync list() on async iterator returns empty
query = firestore.client.collection("sync_jobs").where(...)
job_docs = list(query.stream())  # Returns [] even when docs exist!

# GOOD - use the async helper method
job_docs = await firestore.query_documents(
    collection="sync_jobs",
    filters=[("user_id", "==", user.id)],
    order_by="created_at",
    order_direction="DESCENDING",
    limit=1,
)
```

**Why tests didn't catch this:** Tests mocked `FirestoreService`, not the underlying Firestore client. The mock returned expected data regardless of sync/async usage.

---

### 2026-01-01: dict.get() Returns None When Key Exists with None Value

**Context:** Categorized recommendations endpoint failing with `TypeError: unsupported operand type(s) for /: 'NoneType' and 'float'`.

**Lesson:** `dict.get("key", default)` only returns the default if the key is **missing**. If the key exists but has value `None`, it returns `None`, not the default. This is a common Python gotcha.

**Recommendation:**
```python
# BAD - returns None if doc["spotify_popularity"] is None
spotify_pop = doc.get("spotify_popularity", 50)
score = spotify_pop / 100.0  # TypeError if None!

# GOOD - explicitly handle None
spotify_pop = doc.get("spotify_popularity")
spotify_pop = spotify_pop if spotify_pop is not None else 50
score = spotify_pop / 100.0  # Now safe

# Alternative - use or (but beware of 0 being falsy)
spotify_pop = doc.get("spotify_popularity") or 50  # Only use if 0 is not valid
```

---

### 2026-01-01: Firestore Composite Indexes for Multi-Field Queries

**Context:** Querying user_songs with `has_karaoke_version == False`, `user_id == X`, ordered by `play_count` failing with index error.

**Lesson:** Firestore requires composite indexes for queries that filter on multiple fields or filter + order by different field. These must be created manually - they're not auto-generated.

**Recommendation:**
```bash
# Create composite index via gcloud
gcloud firestore indexes composite create \
  --project=PROJECT_ID \
  --collection-group=user_songs \
  --field-config field-path=has_karaoke_version,order=ASCENDING \
  --field-config field-path=user_id,order=ASCENDING \
  --field-config field-path=play_count,order=DESCENDING

# Or click the link in the error message - Firestore provides direct creation URL
```

**Tip:** The error message includes a direct URL to create the missing index in Firebase Console.

---

### 2026-01-02: Use data-testid for E2E Test Selectors

**Context:** CodeRabbit flagged Playwright tests using brittle selectors like `getByText(/rock/i)` and CSS class selectors that could break when UI text or styling changes.

**Lesson:** Using `data-testid` attributes makes tests more maintainable and resilient to UI changes. Text content, CSS classes, and element structure often change during design iterations, but test IDs remain stable.

**Recommendation:**
```tsx
// Component - add data-testid to key elements
<button data-testid="genre-rock" onClick={...}>
  🎸 Rock
</button>

// Test - use getByTestId instead of text/role selectors
// BAD - breaks if text changes
await page.getByText(/rock/i).click();
await page.getByRole("button", { name: /🎸 Rock/i }).click();

// GOOD - stable selector
await page.getByTestId("genre-rock").click();
```

**Naming convention:**
- Use kebab-case: `genre-rock`, `refresh-artists-btn`
- Include context: `decade-1980s`, `energy-chill`
- For dynamic elements: `genre-${id}`, `progress-dot-${n}`

---

### 2026-01-01: Extract ALL Metadata During ETL, Not Just What You Need Now

**Context:** Original Spotify ETL only extracted basic track/artist data needed for the current feature. Later discovered we needed artist genres and album release dates, requiring a new ETL script.

**Lesson:** Large dataset ETL is expensive (VM time, network, processing). When you have access to a rich data source, extract everything useful in one pass rather than making multiple trips.

**Recommendation:**
1. Before ETL, explore the full schema of your data source
2. Identify ALL potentially useful fields (even for future features)
3. Extract normalized tables that preserve relationships
4. Consider audio features, metadata, and junction tables
5. Store in separate tables - disk is cheap, re-ETL is expensive

```python
# BAD - only extract what you need today
query = "SELECT id, name FROM artists"

# GOOD - extract everything useful
TABLES_TO_EXTRACT = [
    {"table": "artists", "query": "SELECT id, name, popularity, followers..."},
    {"table": "artist_genres", "query": "SELECT artist_id, genre..."},
    {"table": "albums", "query": "SELECT id, release_date, album_type..."},
    {"table": "audio_features", "query": "SELECT track_id, danceability, energy..."},
]
```

**Future-proofing bonus:** Audio features (danceability, energy, valence) enable "high-energy karaoke" or "chill karaoke" filtering without re-ETL.

---

### 2026-01-01: Don't Create "Library" Features Without Library Data

**Context:** Built a "My Songs" / "Songs in Your Library" feature that implied users had song-level listening data. In reality, Spotify only provides top artists (not full listening history), and very few users have Last.fm accounts with scrobble data.

**Lesson:** The UI created a false mental model. Users expected to see songs they'd actually listened to, but instead saw songs from artists they selected in a quiz - making it feel like recommendations, not their actual library. The feature was confusing and misleading.

**Recommendation:**
1. **Be honest about your data.** If you can't get song-level listening history, don't pretend you have it.
2. **Match UI to reality.** Call it what it is: "Artists You Like" not "Your Library"
3. **Show inputs, not illusions.** A "My Data" tab that shows all recommendation inputs (quiz answers, connected services, liked artists) is more honest and useful than a fake library.
4. **Enable experimentation.** When users can see and edit their preference data, they understand how the system works and can tweak it to get better recommendations.

**Better approach:**
```text
# Instead of "My Songs" (implying we have listening data)
# Use "My Data" showing:
- Quiz preferences: genres, decades, energy level
- Artists you like: (source: Spotify / quiz / manual)
- Connected services: what data each provides
- Songs you've rated: loved / hated
```

**See also:** `docs/VISION.md` - "User Data & Profile" section for full design rationale.

---

### 2026-01-02: Debian 12 PEP 668 - Use Virtual Environments

**Context:** Setting up GCE VM for Spotify audio analysis ETL, tried to `pip install` packages globally.

**Lesson:** Debian 12 implements PEP 668 (externally managed Python environment). Running `pip install --user` fails with "externally-managed-environment" error.

**Recommendation:**
```bash
# BAD - fails on Debian 12
pip3 install --user google-cloud-bigquery

# GOOD - use virtual environment
python3 -m venv /data/venv
source /data/venv/bin/activate
pip install google-cloud-bigquery
```

---

### 2026-01-02: ETL Data Reduction - Extract Only What You Need

**Context:** Planning ETL for 4TB Spotify audio analysis torrent.

**Lesson:** The raw audio analysis JSON contains per-beat, per-segment data that accounts for ~99% of the file size. For our use case (tempo/key filtering), we only need the track-level summary fields.

**Recommendation:**
- Before ETL, analyze the data structure to identify what you actually need
- 4TB raw → ~8GB extracted = 99.8% reduction
- Stream-process large files, don't load entirely into memory
- Delete processed files immediately to conserve disk space

---

### 2026-01-02: Preserve Raw Source Data for Future Use

**Context:** Original Spotify metadata ETL only extracted certain fields. Later feature development required fields we hadn't imported, forcing a complete re-download of the 186GB torrent.

**Lesson:** You can't predict all future feature requirements. Extracting "only what you need" saves BigQuery costs but loses data that may be valuable later. Re-downloading large torrents is time-consuming and may become impossible if seeders disappear.

**Recommendation:**
```bash
# After downloading large datasets, upload raw files to GCS Archive storage
# Archive class: $0.0012/GB/month = ~$4.80/month for 4TB
gsutil -m cp -r /data/torrent_folder gs://bucket/raw-archives/

# Use Archive storage class for rarely-accessed data
gsutil mb -c archive -l us-central1 gs://bucket-archive/
```

**Cost comparison:**
- Re-downloading 4TB torrent: 24-48 hours + risk of no seeders
- GCS Archive storage (4TB): ~$5/month
- GCS Coldline storage (4TB): ~$16/month
- GCS Standard storage (4TB): ~$80/month

**Decision:** Always preserve raw source data in cheap archive storage. The monthly cost is negligible compared to re-acquisition risk.

---

### 2026-01-02: Use Cloudflare Worker Proxy to Avoid CORS

**Context:** API requests from `decide.nomadkaraoke.com` to Cloud Run at `karaoke-decide-*.run.app` were failing with CORS errors, especially when the backend returned 500 errors (CORS headers missing on error responses).

**Lesson:** Cross-origin API requests create unnecessary complexity. When you control both frontend and backend, route them through the same origin. Since `decide.nomadkaraoke.com` is behind Cloudflare, a Worker can proxy `/api/*` to Cloud Run.

**Recommendation:**
1. Create a Cloudflare Worker that proxies `/api/*` to Cloud Run
2. Configure route: `decide.nomadkaraoke.com/api/*`
3. Frontend uses relative URLs (`/api/...` instead of full Cloud Run URL)
4. No CORS configuration needed (same-origin requests)

```
Browser → decide.nomadkaraoke.com/api/* → Cloudflare Worker → Cloud Run
```

**Benefits:**
- No CORS issues (same-origin)
- Infrastructure hidden from users
- Can add edge caching, rate limiting later
- Simpler debugging (no cross-origin complexity)

**Files:**
- `infrastructure/cloudflare-worker/api-proxy.js` - Worker code
- `infrastructure/cloudflare-worker/README.md` - Setup instructions
