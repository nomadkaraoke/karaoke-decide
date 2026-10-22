# Vocal range data (Phase 3c, data part)

Per-song and per-artist singing ranges (MIDI note numbers, float) so the mobile app can tell a
singer whether a song fits their voice, with transposition. The fit maths lives in
`karaoke_decide/vocal_range/fit.py`; its shared fixture table `tests/fixtures/voice/fit_cases.json`
is what the mobile TypeScript port must match.

## Sources and precedence

`gen_measured` > `nomad_measured` > `ultrastar` > `gemini` (see `vocal_range/merge.py`).

| Source | What | Where it comes from |
|--------|------|---------------------|
| `gen_measured` | pyin over gen's separated vocals stems (word-timed windows, one pass per singer on duets) | `vocal-range-gen` Cloud Run job, `gs://nomadkaraoke-musicbrainz-data/vocal-range/gen_measured/<run>/part-*.csv` |
| `nomad_measured` | pyin over the NOMAD catalog vocal stems | `scripts/vocal_range/analyze_nomad_stems.py` on NomadPC, result committed as `data/vocal_range/nomad_measured.csv.gz` |
| `ultrastar` | Note charts from the archive.org USDB dump (`usdb_20250204`), octave-repaired against the artist's lead gender. Only derived pitch statistics are kept, never lyrics | fetched once into `gs://.../vocal-range/ultrastar/`, parsed in memory |
| `gemini` | Gemini Flash batch estimates from artist + title only, calibrated per popularity band against measured/UltraStar truth; uncalibrated bands are dropped | `scripts/vocal_range/gemini_batch.py` -> `data/vocal_range/gemini_{songs,artists}.csv.gz` |
| Wikidata P412 | Artist voice types (CC0), used for artist priors | `data/vocal_range/wikidata_p412.csv` (`data/vocal_range/SOURCES.md`) |

### Tiers

`measured` (pyin) > `notes` (UltraStar) > `estimated` (Gemini) > `rough` (artist fallback only).

## Outputs

BigQuery (`nomadkaraoke.karaoke_decide`): `song_vocal_range_sources`, `song_vocal_range`,
`artist_vocal_range` (columns in [DATA-CATALOG.md](DATA-CATALOG.md#vocal-range-tables)).

GCS (`gs://nomadkaraoke-musicbrainz-data/vocal-range/`): `<run>/{song,artist}_vocal_range.tsv.gz`,
`<run>/summary.json`, a `latest/` copy, and `latest.json` (written last:
`{run, song_path, artist_path, sha256_song, sha256_artist}`).

Apps: `catalog.db` (schema 2) gets per-song `vr_*` columns with a rough artist fallback;
`offline.db` (schema stays 1, additive) gets song-level `vr_*` plus an `artist_vocal_range` table.
`song-index-build` reads the TSVs via `latest.json` and checks sha256.

## Running it

- Weekly: runs automatically inside `mb-refresh` just before the catalog build. Skip with
  `--skip-vocal-range`.
- Alone: `gcloud run jobs execute mb-refresh --region us-central1 --args=vocal-range --wait`
  (locally `python -m karaoke_decide.etl.musicbrainz_refresh vocal-range`).
- Measured stems from gen: `gcloud run jobs execute vocal-range-gen --region us-central1 --wait`
  (10 tasks, resumable, skips jobs already in an earlier part file or without a stem; `--limit N`
  locally via `python -m karaoke_decide.etl.vocal_range_gen run --limit N`). It uses
  `Dockerfile.vocal-analysis` (librosa lives only in the optional poetry group `vocal-analysis`,
  never in the main image).
- NomadPC: `nice -n 19 python analyze_nomad_stems.py --in <NOMAD-vocals dir> --out nomad_measured.csv`
  (needs `pitch.py` next to it, resumable, about a 26 h run). Gzip the CSV into
  `data/vocal_range/nomad_measured.csv.gz` and commit.
- Gemini batch (spends money, needs `GOOGLE_AI_STUDIO_KEY` and `VR_GEMINI_DIR` outside the repo):
  `gemini_batch.py prepare` -> `estimate` (token count and cost, hard budget check) -> `submit`
  (only after Andrew's OK) -> `poll` -> `collect`, then commit the two `gemini_*.csv.gz`.

## What degrades gracefully

Every optional input missing (UltraStar zip, a measured CSV, Gemini files, Wikidata) logs one INFO
line and the merge continues with the rest. A failure in the step logs one ERROR and never fails
the MB refresh; BigQuery loads go to `musicbrainz_staging` first so prod tables are untouched on
failure, and `latest.json` is written last so readers keep last week's data. `song-index-build`
and the catalog build fall back to NULL `vr_*` columns with a WARNING when the pointer, a file or
the sha256 is missing.

## Andrew's to-dos after merge

1. `pulumi up` in `infrastructure/` (SA, IAM including the song-index bucket-list clause, the
   `vocal-range-gen` job). Order matters: the first CI run on main must push
   `karaoke-decide-vocal:latest` before the job can be created.
2. `gcloud run jobs execute vocal-range-gen --region us-central1` (about 452 stems exist).
3. `gcloud run jobs execute mb-refresh --region us-central1 --args=vocal-range`, or wait for Sunday.
4. Give the OK for the Gemini batch spend, then run the steps above.
5. Fix the USDB login (needed for any direct USDB fetch beyond the archive.org dump).
6. Commit `nomad_measured.csv.gz` when the NomadPC run finishes.
