# candidates/

Data + output home for the `karaoke-decide candidates` CLI.

- `rejects.jsonl` — **committed** manual reject list (songs Andrew decided not to
  make). Hand-editable; append via `karaoke-decide candidates reject`.
- `cache/` — gitignored runtime caches (Last.fm top tracks, KaraokeNerds full +
  community dumps, LRCLIB per-song ∞, flacfetch per-song ~30d, Last.fm tags).
- `output/` — gitignored generated reports (`candidates.{csv,md,json}`,
  `rejected_misses.csv`, and `singable.{csv,md,json}` from the `singable` mode).

See the tool docs in the repo README and
`docs/archive/2026-08-30-karaoke-candidate-tool-v2-kickoff.md`.
