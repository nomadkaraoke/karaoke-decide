# Cloud Run Secrets Hotfix (2025-12-31)

## Summary

Fixed production magic link authentication failure caused by missing environment variables and secrets in Cloud Run deployment.

## Problem

After merging PR #11 (async music sync), magic link authentication was broken in production:
- `POST /api/auth/verify` returned `{"detail": "JWT_SECRET is not configured"}`
- Cloud Run only had `ENVIRONMENT` and `GOOGLE_CLOUD_PROJECT` env vars set
- Missing: JWT_SECRET, Spotify credentials, Last.fm API key, SendGrid API key

## Root Cause

1. CI workflow (`ci.yml`) only used `--set-env-vars` for plain text env vars
2. Secrets stored in GCP Secret Manager were not mounted to Cloud Run
3. Cloud Run service account lacked IAM permissions to access secrets

## Solution

### 1. Updated CI Workflow
Added `--set-secrets` flag to mount secrets from Secret Manager:
```yaml
--set-secrets "JWT_SECRET=karaoke-decide-jwt-secret:latest,..."
```

### 2. Added IAM Bindings
Granted Cloud Run service account `roles/secretmanager.secretAccessor` on:
- `karaoke-decide-jwt-secret`
- `spotipy-client-id`
- `spotipy-client-secret`
- `lastfm-api-key`
- `sendgrid-api-key`

### 3. Updated Pulumi Infrastructure
Added Secret Manager IAM bindings to `infrastructure/__main__.py` so future deployments maintain access.

## Files Changed

| File | Change |
|------|--------|
| `.github/workflows/ci.yml` | Added `--set-secrets` flag, fixed `GOOGLE_CLOUD_PROJECT` |
| `infrastructure/__main__.py` | Added Secret Manager IAM bindings for Cloud Run SA |
| `docs/LESSONS-LEARNED.md` | Added entries about secrets config and API key security |
| `frontend/e2e/magic-link.spec.ts` | New E2E test for magic link flow (uses Mailslurp) |
| `frontend/.gitignore` | Added Playwright directories |

## Verification

```bash
# Test magic link request
curl -X POST https://karaoke-decide-718638054799.us-central1.run.app/api/auth/magic-link \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com"}'
# Returns: {"message": "If an account exists..."}

# Test token verification
curl -X POST https://karaoke-decide-718638054799.us-central1.run.app/api/auth/verify \
  -H "Content-Type: application/json" \
  -d '{"token": "<token-from-firestore>"}'
# Returns: {"access_token": "eyJ...", "token_type": "bearer", "expires_in": 604800}
```

## Lessons Learned

1. **Cloud Run secrets require both IAM binding AND `--set-secrets`** - the service account needs `secretAccessor` role, and secrets must be explicitly mounted
2. **Test production after every deploy** - even CI passing doesn't guarantee production works
3. **Never hardcode API keys** - E2E test initially had Mailslurp key hardcoded; fixed to use env var
