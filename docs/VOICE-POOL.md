# Voice pool (Phase 3d) runbook

The voice session plays songs from a shared pool of about 1,000 analysed songs. The pool is
built from Spotify audio, separated on karaoke-gen's GPU job and analysed for vocal markers.
The result is `voice.db`, which the API loads behind `voice/latest.json`.

Code: `karaoke_decide/voice_pool/` (select, fetch queue, flacfetch client, separation hand-off,
voice.db) and `karaoke_decide/voice_analysis/` (markers, analyse job). Infra: the "Voice pool"
section of `infrastructure/__main__.py`. Pipeline diagram:
[ARCHITECTURE.md § Voice pool](ARCHITECTURE.md#voice-pool-phase-3d).

## Pieces

| Piece | What | Where |
|-------|------|-------|
| `voice-pool-select` | Cloud Run job, on demand (main image, 2 CPU / 4 GiB, 1 h). Picks the pool, probes YouTube embeddability, queues songs. | us-central1, SA `voice-pool` |
| `voice-pool-fetch` | Cloud Run job, hourly at :07 UTC from scheduler `voice-pool-fetch-hourly` (main image, 1 CPU / 1 GiB, 3900 s). Downloads songs through flacfetch, hands batches to separation, starts analyse. | us-central1, SA `voice-pool` |
| `voice-pool-separate-job` | karaoke-gen's GPU job (1 x L4, `parallelism=1`, 1 h per task). Owned by gen's Pulumi. | us-east4 |
| `voice-pool-analyse` | Cloud Run job, started by fetch (`karaoke-decide-vocal` image, 4 CPU / 8 GiB, 6 h, 1 task). Computes markers, then publishes voice.db. | us-central1, SA `voice-pool` |
| Queue | Firestore `voice_pool_fetch/{pool_id}`; status `queued → downloading → fetched → separating → separated → analysed` (or `failed`) | |
| Audio and stems | `gs://karaoke-gen-storage-nomadkaraoke/voice-pool/{audio,stems,manifests}/` (gen's retention rules don't touch `voice-pool/`) | |
| Outputs | BigQuery `karaoke_decide.voice_pool_songs`, `karaoke_decide.song_voice_analysis` ([DATA-CATALOG.md](DATA-CATALOG.md#voice-pool-tables)); `gs://nomadkaraoke-musicbrainz-data/vocal-range/pool_measured/<run>/part-*.csv` (read by the weekly vocal-range refresh as `pool_measured`); `gs://nomadkaraoke-musicbrainz-data/voice/<run>/voice.db.gz` + `voice/latest.json` (3 runs kept); `voice/selection/<run>/coverage.json` | |

Every job logs at most one ERROR per run. Missing infra (403/404 before `pulumi up`) is a
WARNING and the job exits 0.

## Applying the infra (Andrew)

The voice-pool resources are in decide's Pulumi stack, which other sessions also change.

1. Run Pulumi from an up-to-date `main` checkout, never from a stale branch or worktree.
   `git -C <decide clone> pull` first.
2. Run `pulumi preview --diff` in `infrastructure/` via `/gcp-admin`. The preview must
   **delete nothing** and replace nothing that belongs to another session (lb-poll,
   vocal-range-gen, the search service and so on). If it does, stop: the checkout is
   behind or someone's change is unmerged. If unrelated changes show up, use
   `--target` on the voice-pool resources instead of a full `up`.
3. `pulumi up`. This creates the SA and its grants, the three jobs, the PAUSED scheduler, the
   `nomadkaraoke-voice-recordings` bucket, and the `admin-tokens` accessor grant for the API.
4. The next CI deploy on main pins the three jobs' images (CI skips them with a `::warning::`
   until they exist). To pin them sooner, re-run the latest main deploy workflow.

## First run

```bash
# Select the shared pool (writes the queue, voice_pool_songs and coverage.json)
gcloud run jobs execute voice-pool-select --region us-central1 --args=select,--n,1000 --wait
# Coverage report
gcloud storage cat gs://nomadkaraoke-musicbrainz-data/voice/selection/<run>/coverage.json
```

If the report says `below_minimum` (fewer than 600 songs) or lists empty strata, stop and decide
before fetching. A preview without writes: `--args=select,--n,1000,--dry-run`.

## Pausing and resuming the fetch scheduler

The scheduler is created **paused**. Pulumi ignores its `paused` field afterwards, so a
later `pulumi up` never pauses or resumes it.

```bash
gcloud scheduler jobs resume voice-pool-fetch-hourly --location us-central1   # start hourly fetch
gcloud scheduler jobs pause  voice-pool-fetch-hourly --location us-central1   # stop it
gcloud scheduler jobs describe voice-pool-fetch-hourly --location us-central1 --format='value(state)'
```

Pausing stops new downloads. A run already in progress finishes (at most 3900 s), and a
separation or analyse execution it started runs to completion. To run one fetch by hand:
`gcloud run jobs execute voice-pool-fetch --region us-central1`.

## Status

```bash
gcloud run jobs execute voice-pool-fetch --region us-central1 --args=status --wait
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="voice-pool-fetch"' \
  --limit 20 --freshness 1h --format='value(textPayload)'
```

`status` prints the queue counts per status and the Spotify budget, for example:

```
voice pool fetch queue
  queued 612, downloading 2, fetched 18, separating 20, separated 0, analysed 340, failed 8
  spotify batch 21400/43200 s, total 23100/86400 s (24 h), 1 in flight, gate open
```

Locally, the same command works with `FLACFETCH_API_URL` / `FLACFETCH_API_KEY` set and
`gcloud` user credentials: `poetry run python -m karaoke_decide.voice_pool status`.

Each fetch run also prints a summary in its logs: songs polled, fetched, submitted (and why it
stopped), separated, sent back, sent to separation, and whether analyse was started.

## Spotify budget (flacfetch v0.35.0)

flacfetch enforces the budget itself, so the pool can't crowd out customer downloads:

- Batch (pool) downloads may use **12 h of Spotify audio per rolling 24 h**
  (`FLACFETCH_SPOTIFY_BATCH_BUDGET_S = 43200`).
- All Spotify audio together is capped at 24 h per 24 h. A batch download is refused when
  the 24 h total plus its duration would pass 22 h, which keeps a 2 h reserve for customers.
- At most 2 batch downloads run at once, with at least 30 s between batch starts. Batch
  downloads are refused while a customer (interactive) Spotify download is queued or running.
  Customer downloads are never refused, only counted.
- The fetch job also waits a random 45 to 180 s between submissions, and stops when flacfetch
  answers 429 (`spotify_budget`, `batch_busy`, ...).

Read the budget directly:

```bash
curl -s -H "X-API-Key: $(gcloud secrets versions access latest --secret=flacfetch-api-key)" \
  "$(gcloud secrets versions access latest --secret=flacfetch-api-url)/spotify/budget" | jq
```

Healthy: `batch_s ≤ batch_budget_s` (43200) and `total_s` below `hard_cap_s` (86400). At 12 h a
day, the 1,000-song backfill takes about a week (about 190 songs a day).

## Re-running analysis after an `ANALYSIS_VERSION` bump

`ANALYSIS_VERSION` (`karaoke_decide/voice_analysis/markers.py`, mirrored in
`karaoke_decide/voice_pool/voicedb.py`) is stored on every `song_voice_analysis` row. Readers
take the latest row per `(song_key, analysis_version)`, so old rows can stay.

`analysed` is a terminal queue status, so the analyse job does not pick those songs up again
on its own. There is no `--reanalyse` flag yet. To re-analyse after a marker change:

1. Ship the code change with the bumped `ANALYSIS_VERSION`, and wait for CI to pin the new
   `karaoke-decide-vocal` image on `voice-pool-analyse`.
2. Pause the fetch scheduler (above) so fetch doesn't start a second analyse run halfway.
3. Set the analysed songs back to `separated`. Their stems are still at
   `voice-pool/stems/<pool_id>/vocals.flac`, so no Spotify budget or GPU time is used.
   This step writes to Firestore, so it needs Andrew's credentials (`/gcp-admin`):

   ```python
   from google.cloud import firestore
   db = firestore.Client(project="nomadkaraoke")
   for doc in db.collection("voice_pool_fetch").where("status", "==", "analysed").stream():
       if doc.get("vocals_gcs"):
           doc.reference.update({"status": "separated", "updated_at": firestore.SERVER_TIMESTAMP})
   ```

4. `gcloud run jobs execute voice-pool-analyse --region us-central1 --wait`. One task works
   through every `separated` song, then publishes a new voice.db. Use `--args=analyse,--limit,50`
   for a trial run first.
5. Resume the scheduler.

The weekly vocal-range refresh picks up the rewritten `pool_measured` CSVs on its next run. To
rebuild voice.db alone, without analysing: `--args=publish`.

## Cost

These are estimates. Replace them with measured numbers after the first day (see
"Monitor day 1" in the plan, and `EST_COST_PER_SONG_USD`).

| Item | Estimate |
|------|----------|
| GPU separation (gen, L4) | about $0.012 per song (`EST_COST_PER_SONG_USD`), so about $12 for the 1,000-song pool. |
| Analyse BigQuery | one Spotify sections/features query per batch of up to 200 songs. A dry run on 2026-10-22 scanned 33.6 GiB, about $0.21 on demand. At about 8 analyse runs a day during the backfill, that is about $1.70 a day, or about $12 for the week. |
| Analyse CPU (4 CPU / 8 GiB) | cents per run. |
| Fetch (1 CPU / 1 GiB) | about $0.07 per hour of run time. During the backfill a run can last up to 55 min while the budget is open, so up to about $1.70 a day (about $12 for the week). Once the queue is empty, a run takes seconds. Pause it once the pool is complete. |
| Select | one run with about 2 BigQuery queries. Negligible. |
| Storage | about 30 to 50 MB of FLAC plus stems per song in the gen bucket (about 50 GB for 1,000 songs, about $1 a month). voice.db is small. |

Personal pools (later tasks) carry a global guard of $20 a month of estimated cost.

## Troubleshooting

- **Fetch logs "FLACFETCH_API_URL / FLACFETCH_API_KEY not set; skipping"**: the job isn't
  mounting the `flacfetch-api-url` / `flacfetch-api-key` secrets, or `pulumi up` hasn't run.
- **"can't run voice-pool-separate-job yet (... infra pending?)"**: the `voice-pool` SA lacks
  `run.jobsExecutorWithOverrides` / `run.viewer` on gen's job (Pulumi not applied), or gen's
  job doesn't exist.
- **Songs stuck in `separating`**: after 5 h without a result they go back to `fetched` with
  one more attempt. After 3 attempts they become `failed`. Check the gen job's executions in
  us-east4.
- **`failed` songs**: the queue doc's `error` field says why. Look at the error before retrying a song by hand.
