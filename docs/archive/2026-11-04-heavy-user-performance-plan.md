# Heavy-user performance (50k-song users): plan

Release blocker for mobile use case 4. Spec: BACKLOG INBOX "decide is unusably slow for users with big music histories".

## Evidence (read-only, Andrew's account, 50,790 rows, 2026-11-04)

| Stage | Mac | Prod (1 vCPU) |
|---|---|---|
| Full `user_songs` read (projected) | 4.9 s | ~16 s (`/api/services/summary` 16.3 s cold, 0.95 s once cached, rev 00224) |
| `_map_songs`: 121k SQLite calls, 133k key normalisations | 2.2 s | est. ~7 s |
| get-made `_own_songs`: same mapping again | 2.5 s | est. ~8 s |
| candidates + score + sections | ~0.5 s | est. ~1.5 s |

Before #204 the app's launch overlapped up to 11 of those reads on one vCPU, giving 46-262 s and an OOM. #204 stopped the OOM. The cache only lasts 60 s per instance, so a cold read still costs 16-25 s. Not causes: BigQuery (not in this path), O(n²) joins, cold starts (slow requests hit long-lived instances).

## Design

Heavy users only (>= 5,000 rows); everyone else keeps today's path.

- `user_songs_snapshots/{user_id}`: meta doc (`snapshot_id`, `rev`, `dirty_ids`, `stale`, `catalog_run`, `built_at`, `derived_at`, `row_count`, `shards`).
- `user_songs_snapshot_shards/{user_id}:{snapshot_id}:{i}`: gzip JSON of the projected rows plus each row's derived catalog key (1.4 MB for 50k rows: 2 reads instead of 50,790; decodes in < 0.1 s).
- Every `user_songs` write through `FirestoreService` updates the meta doc before returning: `rev + 1` and the doc ids in `dirty_ids`, or `stale` for bulk writes. No meta doc = no snapshot = no-op. This covers every instance and job, so readers see their own writes.
- Read: 1 meta read. If `(snapshot_id, rev, catalog_run)` is in this instance's memo, answer at once. Otherwise read shards, re-read just the dirty rows (`get_many`), aggregate.
- Refresh through the existing Cloud Tasks queue, so it gets CPU (the service runs with CPU throttling): too many dirty ids or old derivations = rewrite shards with no row reads; `stale` or > 7 days old = one full read. Readers keep the old snapshot meanwhile.
- First read of a heavy user with no snapshot builds it in the request (same cost as today, once).
- Voice sessions and the voice pool keep their fresh reads (3d contract untouched).

## Cost

Reads fall: 50k per cache miss becomes 1 + 2 + dirty. Adds about 1 small write per `user_songs` write for heavy users (a no-op update for others) and ~1.5 MB stored per heavy user. No new infra, no min-instances.

## Verification

`backend/tests/test_user_songs_snapshot.py` (no full read when warm, exactness against the rows path, staleness) and `scripts/bench_heavy_user.py` (50k rows on the Firestore emulator; exits non-zero when p95 > 2 s). Then before/after timings on prod with Andrew's account.
