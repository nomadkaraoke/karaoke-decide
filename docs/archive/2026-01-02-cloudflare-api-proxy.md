# Cloudflare API Proxy - 2026-01-02

## Summary

Added a Cloudflare Worker to proxy API requests from `decide.nomadkaraoke.com/api/*` to the Cloud Run backend. This eliminates CORS issues by keeping frontend and API on the same origin.

## Problem

API requests from the frontend were failing with CORS errors, particularly when the backend returned 500 errors (CORS headers were missing on error responses). The error message shown to users was misleading: "Unable to connect to server. Please check your internet connection..."

## Solution

Instead of fixing CORS headers, we implemented a same-origin proxy:

1. **Cloudflare Worker** - Intercepts `/api/*` requests and forwards them to Cloud Run
2. **Frontend change** - Uses relative URLs (`/api/*`) instead of full Cloud Run URL
3. **Pulumi integration** - Worker and route managed via infrastructure as code

## Architecture

```
Browser → decide.nomadkaraoke.com/api/* → Cloudflare Worker → Cloud Run
       → decide.nomadkaraoke.com/*      → GitHub Pages (unchanged)
```

## Files Changed

| File | Change |
|------|--------|
| `infrastructure/__main__.py` | Added WorkersScript and WorkersRoute resources |
| `infrastructure/requirements.txt` | Added `pulumi-cloudflare` |
| `infrastructure/cloudflare-worker/` | Worker code and setup docs |
| `frontend/src/lib/constants.ts` | Changed API_BASE_URL to empty string (relative URLs) |
| `.github/workflows/ci.yml` | Removed hardcoded NEXT_PUBLIC_API_URL |
| `docs/ARCHITECTURE.md` | Updated system diagram |
| `docs/LESSONS-LEARNED.md` | Added lesson about this pattern |

## Configuration Required

Pulumi config values (set in `Pulumi.prod.yaml`):
- `cloudflare:apiToken` (secret)
- `cloudflareAccountId`
- `cloudflareZoneId`

## Benefits

- **No CORS issues** - Same-origin requests don't need CORS
- **Hidden infrastructure** - Cloud Run URL not exposed to users
- **Future flexibility** - Can add edge caching, rate limiting at Cloudflare

## Related

- `docs/LESSONS-LEARNED.md` - Entry about CORS proxy pattern
- `infrastructure/cloudflare-worker/README.md` - Setup instructions
