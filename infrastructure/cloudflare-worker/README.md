# Cloudflare Worker: API Proxy

This Cloudflare Worker proxies API requests from `decide.nomadkaraoke.com/api/*` to the Cloud Run backend, eliminating CORS issues by keeping everything same-origin.

It also:
- sends `/api/search` and `/api/search/*` to the search service (`SEARCH_BACKEND_URL`, bound by Pulumi), keeping `CF-Connecting-IP` for its rate limits;
- serves `/offline-index/<run-token>/offline.db.gz` from the public `nomadkaraoke-song-index-public` bucket, edge-cached for 7 days (route `decide.nomadkaraoke.com/offline-index/*`). Only that exact path shape is fetched.

Pulumi deploys `api-proxy.js` verbatim. Tests: `node --test infrastructure/cloudflare-worker/` (also run by `tests/unit/test_api_proxy_worker.py`).

## Why?

- **No CORS**: Frontend and API share the same origin
- **Hidden infrastructure**: Cloud Run URL not exposed to users
- **Edge caching**: Can add caching rules in future
- **Flexibility**: Easy to add rate limiting, auth checks, etc.

## Setup with Pulumi (Recommended)

The Worker is managed via Pulumi in `infrastructure/__main__.py`.

### Prerequisites

1. Get your Cloudflare Account ID and Zone ID from the dashboard
2. Create a Cloudflare API Token with Workers permissions:
   - Go to [API Tokens](https://dash.cloudflare.com/profile/api-tokens)
   - Create Token → Edit Cloudflare Workers (template)
   - Add Zone Read permission for your zone

### Configure and Deploy

```bash
cd infrastructure

# Install dependencies (includes pulumi-cloudflare)
pip install -r requirements.txt

# Configure Cloudflare credentials
pulumi config set cloudflare:apiToken <your-api-token> --secret
pulumi config set cloudflareAccountId <your-account-id>
pulumi config set cloudflareZoneId <your-zone-id>

# Preview changes
pulumi preview

# Deploy
pulumi up
```

### Finding Your IDs

Production values are already committed in `infrastructure/Pulumi.prod.yaml`: since
2026-10-14 the Worker lives in the dedicated **Nomad Karaoke** Cloudflare account
(`4585249dcf5d2f9fb4e075c5382cd071`) on zone nomadkaraoke.com
(`2324a342622e93343cf4e08227d82c02`), not Andrew's personal account. The workspace
`.envrc` `CLOUDFLARE_API_TOKEN`/`CLOUDFLARE_ACCOUNT_ID` point at that account.

**Account ID:** Cloudflare Dashboard → any domain → Overview → right sidebar

**Zone ID:** Cloudflare Dashboard → nomadkaraoke.com → Overview → right sidebar → Zone ID

## Manual Setup (Alternative)

If you prefer to set up manually without Pulumi:

### 1. Create the Worker

1. Log into [Cloudflare Dashboard](https://dash.cloudflare.com/) → **Workers & Pages**
2. Click **Create application** → **Create Worker**
3. Name it: `karaoke-decide-api-proxy`
4. Click **Deploy** (with the default code)

### 2. Add the Code

1. Click **Edit code**
2. Replace the default code with contents of `api-proxy.js`
3. Click **Save and deploy**

### 3. Configure Route

1. Go to **Workers & Pages** → your worker
2. Click **Settings** → **Triggers**
3. Under **Routes**, click **Add route**
4. Add: `decide.nomadkaraoke.com/api/*`
5. Select Zone: `nomadkaraoke.com`
6. Click **Save**

## Verify

Test that the proxy works:

```bash
# Should return health check from Cloud Run
curl https://decide.nomadkaraoke.com/api/health

# Expected: {"status":"ok"}
```

## Architecture

```
Browser Request
     │
     ▼
decide.nomadkaraoke.com/api/*
     │
     ▼
┌─────────────────────┐
│  Cloudflare Worker  │
│  (api-proxy.js)     │
└─────────────────────┘
     │
     ▼
Cloud Run Backend
karaoke-decide-*.run.app
```

## Local Development

For local development, the frontend uses `NEXT_PUBLIC_API_URL`:

```bash
cd frontend
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

## Troubleshooting

### Worker not triggering
- Ensure route pattern matches exactly: `decide.nomadkaraoke.com/api/*`
- Check the zone is correct (`nomadkaraoke.com`)
- Verify Worker is deployed (not just saved as draft)

### 502 errors
- Check Cloud Run service is running
- Verify the backend URL in the Worker is correct
- Check Cloud Run logs for errors

### Caching issues
- Add `Cache-Control: no-cache` header if needed
- Or configure cache rules in the Worker
