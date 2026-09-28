# MusicBrainz Auto-Refresh — Plan

**Date:** 2026-09-27
**Status:** Implemented 2026-09-27 (decide v0.8.0). Decisions from Andrew: weekly cadence; refresh *only the data we already have* (no extra raw MB tables kept); Claude runs Pulumi.

### As built (differences from the proposal below)
- **Scope:** no persistent `musicbrainz_raw` dataset. Raw dump tables land in `musicbrainz_staging` only for the run and are deleted after publish. The only additions are what's needed to rebuild existing tables correctly: `url` + `l_artist_url` (for `mbid_spotify_mapping`) and the `*_gid_redirect` tables (new `mb_artist_redirects` / `mb_recording_redirects`, used as a fallback by `get_artist_by_mbid` / `get_recording_by_mbid`).
- **Raw load:** each table loads as exactly N STRING columns (`c0..cN-1`, tab-delimited, no quoting, `\N` = NULL). BigQuery rejects the file if MusicBrainz changes a table's width, so this replaces the `SCHEMA_SEQUENCE` guard. It caught a mis-counted `area` width on the first run.
- **karaoke_recording_links** is rebuilt weekly with the MB refresh (no separate daily run).
- **Health endpoint freshness field:** not built. Freshness is visible via `mb_refresh_log` + the `mb_dump` table label, and failures log ERROR for gen's error monitor (`mb-refresh` added to `MONITORED_CLOUD_RUN_JOBS`); the job also logs ERROR if the last success is >14 days old.
- **First run (dump 20260926-002121):** extract ~20 min (download + lbzip2 on 4 vCPU), load ~1 min, build ~3 min, ~50 GB billed per run. Artists 2.78M → 3.00M, recordings 37.5M → 40.3M, ISRCs 5.5M → 6.4M, Spotify mappings 376K → 455K, karaoke links 162K → 177K. Name churn vs Jan: 0.18% artists, 0.54% recordings; normalization parity exact.
- **Bug found by validation:** BigQuery may evaluate a CTE twice; the ISRC link ranking needed a deterministic tie-break or songs could appear in both the ISRC and name passes (239 duplicate `karaoke_id`s). Fixed, and guarded by the `karaoke_links_unique` check.

**Repo:** karaoke-decide (data consumed by decide backend; tables also backed up by gen's `backup_to_aws`)

## 1. Verification — is the MusicBrainz data stale?

**Yes. The `DATA-CATALOG.md` TODO is accurate: no refresh mechanism exists, and the data is ~8.5 months old.**

| Evidence | Finding |
|---|---|
| Source dump in GCS | Only `gs://nomadkaraoke-musicbrainz-data/raw/20260114-001935/` exists (one dump, ever) |
| `scripts/musicbrainz_etl.py` | Hardcodes `GCS_RAW_PREFIX = "raw/20260114-001935"`; reads a manually uploaded dump; run by hand on a VM |
| BigQuery `lastModifiedTime` | `mb_artists` 2026-01-14, `mb_recordings` 2026-01-15, `mb_artist_tags` 2026-01-14, `mb_recording_isrc` 2026-01-15, `mbid_spotify_mapping` 2026-01-14, `mb_artists_normalized` 2026-01-14, `karaoke_recording_links` 2026-01-15 |
| `mb_recordings_enriched` | Rebuilt 2026-09-26 (PR #139), but **from the January `mb_recordings`**. The new timestamp hides the old data. |
| Cloud Scheduler (`nomadkaraoke`, us-central1) | No MusicBrainz job (KN, divebar, etc. all have daily jobs) |
| Latest upstream dump | `20260926-002121` (MetaBrainz publishes full exports twice weekly, Wed + Sat) |

**Size of the gap (musicbrainz.org/statistics, 2026-09-27):**

| Entity | Ours (Jan 14) | MusicBrainz now | Missing |
|---|---|---|---|
| Artists | 2,780,016 | 2,995,607 | ~215K (7.2%) |
| Recordings | 37,530,321 | 40,341,344 | ~2.8M (7.0%) |

We are also missing edits to existing rows (renames, new ISRCs, tag votes, new Spotify URL relationships) and **entity merges**. When MusicBrainz merges two artists, the losing MBID becomes a redirect, and we store artist MBIDs in Firestore (quiz selections, `user_artists`).

**Second-order staleness: `karaoke_recording_links`.** It joins MB and the KN catalog. `karaokenerds_raw` refreshes daily and now has **300,116** songs. The links were built against **275,809**, so ~24K newer karaoke songs (the most recent releases, which matter most) have no MB link at all.

**Other gaps found:**
- `mb_artists_normalized` was built ad hoc. **Its SQL isn't in the repo** (no script creates it), so it can't be rebuilt reproducibly today.
- `mbid_spotify_mapping` (376K rows) was not built from the dump. `scripts/mlhd_import.py` built it by calling the MB API for the MLHD artist subset only. The dump's `l_artist_url` + `url` tables hold the same Spotify-URL relationships for **every** artist, so building the mapping from the dump is strictly better.

## 2. Goals

1. MB-derived tables are **never more than ~7 days behind** upstream, with no human involved.
2. A refresh can **never leave prod half-updated or empty**: build → validate → publish, and a failed validation keeps the previous data live.
3. **Freshness is observable**: the dump ID and load time can be queried, and an alert fires if data is older than 14 days.
4. **Future features get the MB data they need without a new ETL.** Land a curated set of *raw* MB tables in BigQuery, not just today's hand-picked projections.
5. Old MBIDs stored by users keep resolving after MB merges.
6. Stay cheap. GCP credits expired 2026-09-19 and there's a live cost-reduction effort; target **< $5/month**.

## 3. Approach

### 3.1 Options considered

| Option | Verdict |
|---|---|
| **A. Replication packets** (hourly SQL diffs) | ❌ Packets apply only to a live PostgreSQL mirror with the full MB schema. We'd need to run and maintain a Postgres server (~100GB+) or a `musicbrainz-docker` slave just to diff into BigQuery. Too much ops for weekly-freshness needs. |
| **B. Keep the Python NDJSON ETL, automate it** | ⚠️ Works, but slow: it parses 37M+ rows in single-threaded Python, writes a 9.7GB NDJSON file, then loads it. Every new column or table needs new Python. Not a good base for goal 4. |
| **C. ELT: raw TSV → BigQuery, transform in SQL** | ✅ **Recommended.** Stream the dump, pull out the needed tables as raw TSV into GCS, load them into a `musicbrainz_raw` dataset with **free** BigQuery load jobs, then rebuild every `mb_*` table with SQL. Adding a table later is one line in a list plus a SQL model. |

### 3.2 Architecture (Option C)

```
Cloud Scheduler (weekly, Sun 05:00 ET)
  └─► Cloud Run Job: mb-refresh   (decide repo, image built in CI)
        1. CHECK      read fullexport/LATEST; exit 0 early if already loaded (mb_refresh_log)
        2. DOWNLOAD   stream mbdump.tar.bz2 + mbdump-derived.tar.bz2 over HTTPS,
                      verify SHA256SUMS, decompress with lbzip2 (parallel)
        3. EXTRACT    stream-read the tar; for each allowlisted member
                      (mbdump/<table>), stream the bytes to
                      gs://nomadkaraoke-musicbrainz-data/raw/<dump_id>/<table>.tsv
                      (nothing lands on local disk; Cloud Run disk is RAM)
        4. LOAD       BigQuery load jobs (free) into musicbrainz_staging.<table>
                      CSV, field_delimiter='\t', quote='', null_marker='\N',
                      schema from a checked-in schema file per table
        5. TRANSFORM  run SQL models → karaoke_decide_staging.mb_* (CTAS)
        6. VALIDATE   hard checks (below); on failure: log, alert, exit non-zero,
                      prod untouched
        7. PUBLISH    BigQuery copy jobs (free, per-table atomic)
                      staging → musicbrainz_raw.* and karaoke_decide.mb_* ;
                      set table labels mb_dump=<dump_id>
        8. RECORD     insert row into karaoke_decide.mb_refresh_log
        9. GC         delete gs://…/raw/<dump_id>/ older than the last 2 dumps
                      (GCS lifecycle rule)
```

**Why a Cloud Run Job, not a VM:** no instance to manage, no disk to fill, and billing is per-second only while it runs. It streams everything, so memory stays small: 4 vCPU / 8 GiB, task timeout 3h. Expected runtime is ~45–75 min, dominated by bz2 decompression of ~7GB compressed (~60GB raw). Fallback if streaming decompression is too slow: a spot VM with a startup script (the `divebar-sync-vm` pattern).

**Why weekly:** upstream publishes twice weekly, and weekly keeps us ≤7 days behind at half the cost. Moving to twice weekly (Wed + Sat, after the ~05:00 UTC publish) is a one-line cron change.

### 3.3 Raw tables to land (`musicbrainz_raw` dataset)

These are loaded verbatim with MB's column names, so future work can query real MB structure:

- **Core:** `artist`, `artist_type`, `area`, `gender`, `artist_alias`, `artist_credit`, `artist_credit_name`
- **Recordings / releases:** `recording`, `isrc`, `track`, `medium`, `release`, `release_group`, `release_group_primary_type`, `release_country`, `release_unknown_country`
- **Works (writers and covers, handy for karaoke):** `work`, `l_recording_work`, `iswc`
- **URLs (Spotify, YouTube, Wikidata links):** `url`, `l_artist_url`, `l_recording_url`, `link`, `link_type`
- **Redirects (merges):** `artist_gid_redirect`, `recording_gid_redirect`, `release_group_gid_redirect`
- **Derived dump:** `tag`, `artist_tag`, `recording_tag`, `release_group_tag`, `genre`, `artist_meta`, `recording_meta` (ratings)

Estimated BigQuery storage is ~25–35 GB, about $0.50–0.70/month (long-term pricing after 90 days is roughly half). The allowlist lives in one Python constant. Adding a table means adding its name and schema.

Schemas come from MB's `admin/sql/CreateTables.sql` at the dump's `SCHEMA_SEQUENCE`. The job **checks `SCHEMA_SEQUENCE`** and fails loudly (prod untouched) if it differs from the version our schema files target. MB schema changes happen about once a year, with advance notice.

### 3.4 SQL models → the existing `karaoke_decide` tables

Each model is a checked-in `.sql` file (e.g. `etl/musicbrainz/models/*.sql`) run in dependency order:

| Target table | Built from | Notes |
|---|---|---|
| `mb_artists` | artist ⋈ artist_type ⋈ area ⋈ gender | Same columns as today, plus `name_normalized` |
| `mb_artist_tags` | artist_tag ⋈ tag ⋈ artist | Same schema |
| `mb_recordings` | recording ⋈ artist_credit | Same schema, same Python-compatible normalization |
| `mb_recording_isrc` | isrc ⋈ recording | Same schema |
| `mbid_spotify_mapping` | l_artist_url ⋈ url (`open.spotify.com/artist/…`) ⋈ artist | **Improvement**: covers every artist, not just the 376K MLHD subset. Keeps `artist_name`. |
| `mb_artists_normalized` | mb_artists ⋈ mbid_spotify_mapping ⋈ spotify_artists_normalized ⋈ mb_artist_tags | **SQL must be reconstructed.** Reverse-engineer from the current table and diff against the current output (see Phase 1) |
| `mb_artist_redirects` / `mb_recording_redirects` | *_gid_redirect ⋈ entity | **New**: `old_mbid → current_mbid` |
| `mb_recordings_enriched` | existing SQL from `create_cost_optimized_tables.py` | Move into the model set so it always rebuilds after `mb_recordings` |
| `karaoke_recording_links` | port `link_karaoke_to_recordings.py` (ISRC + exact-name strategies) to SQL | Both strategies are already BigQuery queries, and the Python only shuttles rows into NDJSON. Porting it removes the local-disk step. |
| `isrc_spotify_mapping` | view | Nothing to do; it's already live over `mb_recording_isrc` |

`create_cost_optimized_tables.py` keeps building `spotify_popularity_by_artist_title` (Spotify-only, static). `mb_recordings_enriched` moves into the MB pipeline.

**KN link freshness:** `karaoke_recording_links` goes stale when KN adds songs, not only when MB changes. So the job also runs **models-only mode** (skip steps 1–4, rebuild `karaoke_recording_links`, validate, publish) daily after `kn-data-sync-full-daily` (04:30 ET), at ~06:30 ET. The query scans ~5–8 GB, about $0.04/day.

### 3.5 Validation gates (step 6: any failure leaves prod as-is)

- Every staging table is non-empty.
- Row counts are ≥ 99% of the current prod table (MB almost never shrinks) and ≤ 115% (a large jump means a parsing bug). Configurable per table.
- Canary MBIDs are present with the expected names: Radiohead `a74b1b7f-…`, Green Day `084308bd-…`, plus a handful of recordings, ISRCs, and a Spotify mapping.
- `mbid_spotify_mapping` row count ≥ current 376K. It should jump well above this the first time.
- `karaoke_recording_links` coverage (% of KN songs linked) doesn't drop by more than 1 percentage point.
- Normalization parity: sample 1,000 `name_normalized` values and compare with the Python `_normalize_for_matching` in a unit test fixture (guards against SQL/Python drift, which would silently break search).

### 3.6 Merges: keeping stored MBIDs resolvable

- The new `mb_artist_redirects` table is published every refresh.
- `BigQueryCatalogService.get_artist_by_mbid` / `get_artists_metadata` / `batch_lookup_*` fall back through `mb_artist_redirects` when an MBID misses (one extra small clustered lookup, only on a miss).
- Optional follow-up: after each refresh, a Firestore sweep rewrites redirected MBIDs in `quiz_artist_mbids` / `user_artists`. Not needed at launch because the read-path fallback already covers it.
- The same applies to `mlhd_artist_similarity` (static, keyed by 2026-01 MBIDs): resolve through redirects at query time.

### 3.7 Observability

- **`karaoke_decide.mb_refresh_log`** has one row per run: `dump_id, schema_sequence, started_at, finished_at, status, mode (full|models), row_counts JSON, error`.
- Table labels `mb_dump=<dump_id>` on every published table, so staleness shows in `bq show`.
- **Alerting**: the Cloud Run Job failure goes to the existing error-monitor path (Cloud Logging severity=ERROR, already digested daily). Plus a freshness check: decide `/api/health` (or the `status` repo monitor) reports `mb_dump_age_days` and warns above 14.
- `DATA-CATALOG.md` swaps its TODO for "refreshed weekly (see `mb_refresh_log`)" and a query that shows the current dump.

### 3.8 Cost estimate (monthly)

| Item | Estimate |
|---|---|
| Cloud Run Job, 4 vCPU/8 GiB × ~1h × 4 runs | ~$1.00 |
| Egress from metabrainz.org | $0 (ingress is free) |
| GCS: 2 dumps of raw TSV kept (~60 GB each, lifecycle-deleted) | ~$1.50 → keep only the latest, or delete right after load: ~$0.30 |
| BigQuery load jobs | $0 |
| BigQuery storage, raw (~30 GB) + derived (existing) | ~$0.60 incremental |
| BigQuery transform queries (~60–80 GB scanned per full run × 4) | ~$1.50–2.00 |
| Daily KN-link rebuild (~6 GB × 30) | ~$1.10 |
| **Total** | **~$4–6/month** |

Also delete the obsolete 9.7GB `processed/mb_recordings.ndjson` and related NDJSON files once the new pipeline is live (~11 GB of GCS).

## 4. Implementation phases

### Phase 1: Pipeline code (local-runnable, no infra)
- `etl/musicbrainz/` package: `refresh.py` (click CLI: `run --mode full|models`, `--dump-id`, `--dry-run`, `--skip-publish`), `schemas/*.json`, `models/*.sql`, `validate.py`.
- Streaming download + SHA256 verification + tar member allowlist → GCS.
- SQL models for every table in §3.4, including the **reconstructed `mb_artists_normalized`**. Run against the current January staging copy and **diff output vs current prod tables** (row count + `EXCEPT DISTINCT` sample). They should be identical except for documented improvements.
- Port `link_karaoke_to_recordings.py` to SQL and diff against the current `karaoke_recording_links`.
- Unit tests: tar-member filtering, TSV/`\N` edge cases (embedded tabs are escaped as `\t` in MB dumps, and loads must use `quote=''`), validation logic, normalization parity.
- **First manual run** against dump `20260926-002121` with `--skip-publish`, reviewing the staging diffs. Then publish by hand. This alone fixes today's staleness.

### Phase 2: Infra (Pulumi in `karaoke-decide/infrastructure`)
- Artifact Registry image `mb-refresh` (built + pushed by `ci.yml` on main, alongside the backend).
- Service account `mb-refresh@` with `bigquery.dataEditor` (on `karaoke_decide`, `karaoke_decide_staging`, `musicbrainz_raw`, `musicbrainz_staging` only), `bigquery.jobUser`, and `storage.objectAdmin` on the MB bucket.
- Datasets `musicbrainz_raw`, `musicbrainz_staging`, `karaoke_decide_staging` (staging tables expire after 7 days).
- `cloudrunv2.Job` + two `cloudscheduler.Job`s (weekly full, daily models-only).
- GCS lifecycle rule on `raw/` prefixes.
- ⚠️ Decide's Pulumi isn't applied by CI and local ADC is read-only (`claude-readonly@`). **Andrew runs `pulumi up`**, or we add it to a workflow.

### Phase 3: Consumers + observability
- Redirect fallback in `bigquery_catalog.py` + tests.
- `mb_refresh_log`, table labels, health-endpoint freshness field, alert wiring.
- Update `DATA-CATALOG.md`, `MUSICBRAINZ-MIGRATION-PLAN.md` Phase 8, and gen's `backup_to_aws` table list (add the redirect tables; consider exporting `musicbrainz_raw` monthly or skipping it, since it can be re-downloaded).
- Retire `scripts/musicbrainz_etl.py` + `link_karaoke_to_recordings.py` (or reduce them to thin wrappers around the new CLI).

### Phase 4: Watch
- Watch 2–3 scheduled runs. Confirm `mb_refresh_log` rows, row-count deltas (~+0.2%/week expected), cost in billing, and that search latency/bytes-per-query are unchanged (clustering preserved).

## 5. Risks

| Risk | Mitigation |
|---|---|
| Streaming bz2 decompression too slow for Cloud Run | Parallel `lbzip2`; 3h timeout; fall back to a spot VM |
| MB schema change breaks the load | `SCHEMA_SEQUENCE` guard, fail closed; MB announces schema releases months ahead |
| SQL normalization drifts from Python → search misses | Parity unit test + validation-gate sample |
| Reconstructed `mb_artists_normalized` differs subtly (popularity default, tag ordering) | Phase 1 diff against current prod before first publish |
| Partial publish (some tables new, some old) | Publish only after **all** validations pass; copy jobs are fast (seconds); `mb_refresh_log.status` records the outcome |
| Bigger `mbid_spotify_mapping` changes recommendation behavior | Intended improvement; spot-check quiz/recs after first publish |
| Cost creep | Byte caps (`bq_limits.py` pattern) on transform queries; the table in §3.8 is checked in Phase 4 |

## 6. Open questions for Andrew

1. **Cadence:** weekly (recommended) or twice weekly to match upstream?
2. **Raw-table scope:** is the §3.3 allowlist enough, or do you already have features in mind (e.g. writers/works, release years, YouTube URL relationships, aliases for search)? These are cheap to add now.
3. **Infra apply:** will you run `pulumi up` for decide by hand, or should Phase 2 add a Pulumi step to CI?
