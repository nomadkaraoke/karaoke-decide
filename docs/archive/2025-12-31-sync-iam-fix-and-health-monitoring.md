# Sync IAM Fix & Health Monitoring

**Date:** 2025-12-31
**PR:** #16
**Branch:** `feature/20251231-115843`

## Summary

Fixed production sync button 403 error and added infrastructure health monitoring to catch similar issues in the future.

## Problems Addressed

### 1. Sync Button 403 IAM Error

**Symptom:** Clicking "Sync Now" in the UI returned:
```
403 The principal (user or service account) lacks IAM permission 'iam.serviceAccounts.actAs' for the resource '718638054799-compute@developer.gserviceaccount.com'
```

**Root Cause:** Cloud Tasks OIDC authentication requires the service account creating tasks to have `roles/iam.serviceAccountUser` permission on the target service account.

**Fix:** Added IAM binding in `infrastructure/__main__.py`:
```python
gcp.serviceaccount.IAMMember(
    "compute-sa-act-as-self",
    service_account_id=f"projects/{project}/serviceAccounts/{SA_EMAIL}",
    role="roles/iam.serviceAccountUser",
    member=f"serviceAccount:{SA_EMAIL}",
)
```

### 2. Sync Status Not Persisting

**Symptom:** After sync started (202 response), reloading the page showed "Sync Now" button instead of sync progress.

**Root Cause:** `get_sync_status` used `list(query.stream())` on an async Firestore client. The async iterator returned empty results when called synchronously.

**Fix:** Changed to use `firestore.query_documents()` async helper method.

## New Features Added

### Deep Health Endpoint (`/api/health/deep`)

Validates connectivity to all infrastructure:
- Firestore (count documents)
- BigQuery (get catalog stats)
- Cloud Tasks (verify queue exists)

Returns detailed status for each component.

### Scheduled Health Monitoring

GitHub Actions workflow (`.github/workflows/health-monitor.yml`):
- Runs every 6 hours
- Calls `/api/health/deep` endpoint
- Sends email via SendGrid on failure

### Comprehensive E2E Tests

`frontend/e2e/production-comprehensive.spec.ts`:
- Magic link authentication via Mailslurp
- Public feature tests (search, catalog)
- Authenticated feature tests (services, sync, recommendations)
- API health validation

`frontend/e2e/sync-integration.spec.ts`:
- API-level sync tests
- UI sync button tests with auth token

## Testing Gap Identified

Tests mocked infrastructure dependencies completely, so IAM permission errors weren't caught. Now mitigated by:
1. Deep health endpoint for infrastructure validation
2. Scheduled monitoring with alerts
3. Authenticated production E2E tests

## Files Changed

- `infrastructure/__main__.py` - IAM binding for Cloud Tasks OIDC
- `backend/api/routes/services.py` - Async query fix, logging
- `backend/api/routes/health.py` - Deep health endpoint
- `backend/tests/test_health.py` - Deep health tests
- `.github/workflows/health-monitor.yml` - Scheduled monitoring
- `frontend/e2e/sync-integration.spec.ts` - Sync tests
- `frontend/e2e/production-comprehensive.spec.ts` - Full E2E suite
- `docs/TESTING.md` - Testing documentation updates
- `docs/LESSONS-LEARNED.md` - New lessons added

## Key Lessons

1. Cloud Tasks OIDC requires `iam.serviceAccounts.actAs` permission
2. `FirestoreService.AsyncClient.stream()` returns async iterator - can't use sync `list()`
3. Mocked tests don't catch IAM/infrastructure issues - need production health checks
