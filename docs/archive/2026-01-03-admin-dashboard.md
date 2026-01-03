# Admin Dashboard - 2026-01-03

## Summary

Built an admin dashboard for Nomad Karaoke Decide that provides visibility into users, sync jobs, and system health. The dashboard is accessible at `/admin` for users with the `is_admin` flag set.

## Key Changes

### Backend
- Added `is_admin: bool = False` field to User model
- Created `AdminUser` dependency that returns 403 for non-admins
- New admin routes module (`backend/api/routes/admin.py`):
  - `GET /api/admin/stats` - Dashboard statistics
  - `GET /api/admin/users` - Paginated user list with search/filter
  - `GET /api/admin/users/{id}` - User detail with services and sync history
  - `GET /api/admin/sync-jobs` - Paginated sync job list with status filter
  - `GET /api/admin/sync-jobs/{id}` - Sync job detail with progress/results
- Admin response models in `backend/models/admin.py`
- 17 tests for admin routes in `backend/tests/test_admin.py`

### Frontend
- `AdminPage` guard component - redirects non-admins
- Admin layout with sidebar navigation
- Admin pages:
  - `/admin` - Overview with stats cards
  - `/admin/users` - User list with search, filter, pagination
  - `/admin/users/detail?id=xxx` - User detail page
  - `/admin/sync-jobs` - Sync job list with status filter, pagination
  - `/admin/sync-jobs/detail?id=xxx` - Sync job detail with auto-refresh
- Added Shield icon for admin badge
- Added Badge variants: `danger`, `primary`, `secondary`
- Admin link in navigation (visible only for admins)

## Technical Decisions

### URL Structure (Query Parameters vs Dynamic Segments)

**Issue:** Next.js 16 with `output: export` (static export for GitHub Pages) does not support dynamic route segments (`[id]`) without pre-generating all possible paths at build time.

**Solution:** Use query parameters instead:
- `/admin/users/detail?id=xxx` instead of `/admin/users/[id]`
- `/admin/sync-jobs/detail?id=xxx` instead of `/admin/sync-jobs/[id]`

This is a known limitation of Next.js static exports (see [GitHub Issue #79380](https://github.com/vercel/next.js/issues/79380)).

### Testing Pattern (FastAPI dependency_overrides)

For admin route tests, used FastAPI's `app.dependency_overrides` pattern for clean mocking:

```python
@pytest.fixture
def admin_client(mock_firestore: MagicMock, admin_user: User):
    from backend.api.deps import get_current_user, get_firestore
    from backend.main import app

    async def override_get_current_user():
        return admin_user

    async def override_get_firestore():
        return mock_firestore

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_firestore] = override_get_firestore

    yield TestClient(app)

    app.dependency_overrides.clear()
```

This is cleaner than patching module paths and ensures the overrides are properly scoped to each test.

## Setting Admin Users

Admin status is set manually in Firestore:

```bash
# Via Firebase console or CLI
firebase firestore:update users/<USER_ID> --data '{"is_admin": true}'
```

Or via Python:
```python
db.collection("users").document(user_id).update({"is_admin": True})
```

## Files Added/Modified

### New Files
- `backend/api/routes/admin.py` - Admin routes
- `backend/models/admin.py` - Admin response models
- `backend/tests/test_admin.py` - Admin route tests
- `frontend/src/components/AdminPage.tsx` - Admin guard
- `frontend/src/app/admin/layout.tsx` - Admin layout
- `frontend/src/app/admin/page.tsx` - Overview dashboard
- `frontend/src/app/admin/users/page.tsx` - Users list
- `frontend/src/app/admin/users/detail/page.tsx` - User detail
- `frontend/src/app/admin/sync-jobs/page.tsx` - Sync jobs list
- `frontend/src/app/admin/sync-jobs/detail/page.tsx` - Sync job detail

### Modified Files
- `karaoke_decide/core/models.py` - Added `is_admin` field
- `backend/api/deps.py` - Added `AdminUser` dependency
- `backend/api/routes/__init__.py` - Registered admin routes
- `frontend/src/contexts/AuthContext.tsx` - Added `is_admin` support
- `frontend/src/lib/api.ts` - Added admin API methods
- `frontend/src/components/Navigation.tsx` - Added admin link
- `frontend/src/components/ui/Badge.tsx` - Added variants
- `frontend/src/components/icons/index.tsx` - Added ShieldIcon

## Test Results
- Backend tests: 348 passed
- Unit tests: 135 passed
- Frontend build: Successful

## Future Considerations

1. **Audit logging** - Track admin actions for compliance
2. **Admin actions** - Add ability to trigger user sync, reset data, etc.
3. **Real-time updates** - WebSocket for live sync job progress
4. **Export functionality** - Export user data, sync logs as CSV
5. **Admin role levels** - Different permissions for different admin types
