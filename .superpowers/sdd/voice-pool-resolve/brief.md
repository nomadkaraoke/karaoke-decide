# Fix: voice pool fetch fails items that have no spotify_id (decide)

## Incident (prod, 2026-10-25)
Firestore `voice_pool_fetch`: all 104 items with `source == "andrew_sheet"` are `status: failed`, `error: "no spotify id"`, `attempts: 0`, `spotify_id: None`, `duration_ms: None`. They were enqueued by `scripts/voice/import_sheet.py` → backend import endpoint → `karaoke_decide/voice_pool/queue.enqueue()` with only song_key/artist/title/source. `fetch._unfetchable()` fails anything without `spotify_id` (or with a duration outside 1..MAX_EXPECTED_DURATION_S) immediately. Shared items work because `select.py` resolves `(spotify_id, duration_ms)` from the song's KaraokeNerds ids via BigQuery (`SPOTIFY_FOR_KN_SQL`, `_spotify_tracks`).

## Required change
1. **Resolve in the fetch job.** Before submitting, for queued items missing `spotify_id` or `duration_ms`, resolve them in ONE batched lookup per run (reuse `select._spotify_tracks` / its SQL — extract a shared helper rather than duplicating; find how select maps song_key → KN ids from the catalog and reuse that too). Write the resolved `spotify_id`/`duration_ms` onto the doc. Only items that still can't be resolved fail with `"no spotify id"` (WARNING log, aggregated count per run — not one ERROR per song; see workspace CLAUDE.md alert rules). This is also what Part F (personal pools) needs: "resolve missing Spotify ids for personal items in the job".
2. **Repair script** `scripts/voice/requeue_unresolved.py` (pattern: `scripts/voice/repair_failed_vocals.py`): `--dry-run` default-safe listing; `--apply` resets docs with `status == failed` and `error == "no spotify id"` back to `status: queued`, `error: None`, keeping priority/source. Print counts.
3. Tests (TDD): unit tests in `tests/unit/voice_pool/test_fetch.py` (resolution fills ids and the item is submitted; unresolvable still fails with one aggregated warning; already-resolved items untouched, no BQ query when nothing to resolve) and a test for the repair script logic.
4. Bump patch version 0.28.2 → 0.28.3 in pyproject.toml (and anywhere else the repo keeps the version — check how #189 bumped). Update docs/VOICE-POOL.md briefly (resolution step + repair script).

## Rules
Work only in /Users/andrew/Projects/nomadkaraoke/karaoke-decide-voice-pool-resolve. Read its CLAUDE.md. Run `poetry run pytest tests/unit/voice_pool -q` and the repo's lint/type checks (see CLAUDE.md / Makefile). No subagents, no push, no bare `git stash`. Commit with conventional messages ending with:
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188txBF47GpTo7hym1FoifX
Write the report to report.md beside this brief; return only status, commits, one-line test summary, concerns. Do not run anything against prod.
