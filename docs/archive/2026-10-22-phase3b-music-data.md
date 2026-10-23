# Phase 3b: music data (decide side)

Shipped 2026-10-22 for the mobile Phase 3b "music data" work. Everything on the decide side is merged and deployed except this final PR (v0.26.0, hardening and docs).

## What shipped

| PR | Version | Contents |
|----|---------|----------|
| #177 | 0.21.0 | Part A: imports API. Screenshot import (Gemini Flash), public Spotify playlists, Spotify/Apple privacy-export aggregates, `/api/services/summary`, `/sources/{source}/data`, `/capabilities`, token cipher (`DECIDE_TOKEN_KEY`, Fernet). |
| #179 | 0.22.0 | Part B: ListenBrainz connect via MetaBrainz OAuth (PKCE), Spotify-export upload to ListenBrainz, OIDC-verified `/internal/*`, daily `lb-poll` Cloud Run Job, Worker manual redirects. |
| #182 | 0.24.0 | Part C: sync resume and fencing (`stalled` status), Last.fm resume, Spotify reconnect, Cloud Tasks queue retries, bounded ingest executor (event-loop capacity). |
| this PR | 0.26.0 | Part D: local-stack fakes (`DECIDE_FAKE_EXTERNALS`, `DECIDE_INLINE_TASKS`) and their hardening, lazy `TrackMatcher.catalog_service` lock, infra/CI parity test, docs. |

Hardening in this PR: both local-only flags are refused whenever `environment != "development"`; production refuses the committed test-only Fernet key (sha256 comparison); inline tasks log that `delay_seconds` is ignored; `TrackMatcher.catalog_service` is created once under a lock.

## Infra applied (Pulumi, admin session)
- Part B `pulumi up`: 16 created, 3 updated (lb-poll Cloud Run Job, Scheduler `lb-poll-daily` 06:30 UTC and its SA, grants for the 4 API secrets, api-proxy Worker manual redirects, API service env/secrets).
- Part C `pulumi up`: 2 updated (Cloud Tasks queue retry 10 attempts, 60 s to 600 s backoff).
- `decide-token-key` secret and its first version were created by hand (not a Pulumi resource); `gemini-api-key` and `metabrainz-client-*` already existed. `REQUIRED_SECRETS` grants all four.
- ci.yml mounts the secrets unconditionally and sets `LISTENBRAINZ_OAUTH_ENABLED=true` and `PUBLIC_API_BASE_URL`; `test_main_service_secrets_have_pulumi_access` keeps ci.yml and Pulumi in step.
- Repo secret `DISCORD_NOISE_WEBHOOK_URL` set.

## Prod verification done
- After A: capabilities `{screenshot:false, lb_oauth:false}` until infra, `/summary` 401 unauthenticated, health ok.
- After B: OAuth start 401 unauthenticated; callback with a bad state 302s to the web error page; capabilities both true; lb-poll job on the merged image, scheduler enabled; manual execution succeeded (0 connections, catalog loaded in 9 s).
- After C: deploy and E2E smoke green, health ok, `/sync/status` 401 unauthenticated.

## Capacity diagnosis (Task 27)
The three 0 s 500s seen during an Apple sync on 2026-10-22 were Cloud Run routing requests to retired revision 00192 (no instances) after a deploy, before any instance was assigned. They were not caused by sync load: CPU p99 stayed low, memory 24 to 32 percent, concurrency far below the limit, and `/api/health` stayed under 20 ms during the sync. Real event-loop blocking was found and fixed anyway (an 11 s synchronous BigQuery artist lookup in the Last.fm step; the TrackMatcher BigQuery fallback). Blocking steps now run on a 2-thread `ingest` executor. No infra change.

## Remaining
- Nothing required from Andrew.
- Optional: ask MetaBrainz (registration-requests / connect-services) to list the app so users see a vetted consent page.
- Backlog from the diagnosis: watch for retired-revision misroutes; batch the per-track Firestore writes in `_upsert_user_songs`; recs v2 is slow (29 to 50 s) for heavy users.
- First real-user check: the next scheduled `lb-poll` run (06:30 UTC) logs a `listenbrainz poll` INFO summary.
