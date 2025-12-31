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
