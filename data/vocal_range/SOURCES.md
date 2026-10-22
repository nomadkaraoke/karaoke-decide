# Vocal range data sources

| File | Source | Licence / notes |
|------|--------|-----------------|
| `wikidata_p412.csv` | Wikidata property P412 (voice type) joined to MusicBrainz artist IDs (P434), SPARQL query run 2026-10-21; columns `mbid,vtLabel`, 11,491 rows | CC0 |
| UltraStar notes | archive.org `usdb_20250204` (USDB dump) | see `karaoke_decide/vocal_range/ultrastar.py` |
| `gemini_songs.csv.gz`, `gemini_artists.csv.gz` | Gemini batch estimates (artist + title only in prompts, no lyrics), model `gemini-3.6-flash`, thinking budget 0, via `scripts/vocal_range/gemini_batch.py`; run date: pending (not yet run) | Model output; low-confidence estimates, kept only per popularity band after calibration against measured/UltraStar data |
