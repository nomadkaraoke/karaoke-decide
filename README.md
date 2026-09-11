# Nomad Karaoke Decide

**Help people discover and choose the perfect karaoke songs based on their music listening history.**

> decide.nomadkaraoke.com

## What is this?

Nomad Karaoke Decide helps you answer the eternal karaoke question: "What should I sing?"

By connecting your music streaming accounts (Spotify, Last.fm), we match your listening history against available karaoke songs, so you can find songs you actually know and love.

## Features

- **Know what you know** - See which karaoke songs match your listening history
- **Search the catalog** - Browse thousands of karaoke songs from multiple sources
- **Build playlists** - Create karaoke playlists for your next session
- **Track your performances** - Record which songs you've sung and rate them

## Quick Start

### CLI Installation

```bash
# Install with pip
pip install karaoke-decide

# Or with pipx (recommended)
pipx install karaoke-decide
```

### Basic Usage

```bash
# Log in
karaoke-decide auth login

# Connect your Spotify account
karaoke-decide services connect spotify

# Sync your listening history
karaoke-decide services sync

# Find songs you know
karaoke-decide songs mine

# Search the catalog
karaoke-decide songs search "bohemian rhapsody"
```

### Karaoke-job candidates (internal tool)

Find good songs to *make* as Nomad karaoke jobs from Andrew's Last.fm history —
songs he plays a lot, that have **no existing community/Nomad karaoke version**,
that we can **fully characterize** (Spotify audio features), that have **real
singable lyrics** (LRCLIB), that an **LLM judges to be a good karaoke song**, and
that can be **sourced as a high-quality FLAC** (flacfetch). Designed to be run by
any agent to get "the next N real karaoke tracks to make".

```bash
# The next 5 songs worth making (pure Last.fm-playcount order among survivors)
karaoke-decide candidates suggest --count 5

# JSON output, ready to feed into the gen create-job flow
karaoke-decide candidates suggest --count 5 --format json

# Loosen/tighten the cheap suitability pre-filter (default 45; LLM is the real gate)
karaoke-decide candidates suggest --count 5 --min-score 40

# Filter by genre/tag (see "Genre filter" below): skip all electronic, only rock
karaoke-decide candidates suggest --count 5 -x electronic
karaoke-decide candidates suggest --count 5 -g rock

# Mark a song you've decided not to make (never suggested again)
karaoke-decide candidates reject "Pendulum" "Slam" --reason "too repetitive live"
karaoke-decide candidates review-rejects
```

**The inverse — songs you already *can* sing.** `singable` lists your most-played
Last.fm tracks that **already have a community karaoke version** (KaraokeNerds
`karaokenerds_community` — free, YouTube-playable versions like NOMAD/WTF), so you
get a "go sing these tonight" list instead of a "make these" list. No production
gates (Spotify/lyrics/LLM/flacfetch) run — a version already exists, so suitability
and sourcing don't apply; it's a pure playcount-ranked intersection, enriched with
the brands that carry it and a watch link.

```bash
# Top 50 played songs that already have a community karaoke version
karaoke-decide candidates singable

# Tighten to your heaviest rotation, JSON for scripting
karaoke-decide candidates singable --count 20 --min-plays 20 --format json

# Filter by genre/tag: keep drum & bass and rock, drop anything classical
karaoke-decide candidates singable -g "drum and bass" -g rock -x classical
```

Output columns: playcount · artist · title · brands · versions · watch (youtu.be
link where available) · genres. Reports written to
`candidates/output/singable.{csv,md,json}`.

**Genre filter** (on both `suggest` and `singable`; `-g/--genre` to include,
`-x/--exclude-genre` to exclude, both repeatable): matches the artist's genre/tag
descriptors — **MusicBrainz-first** (`mb_artists_normalized.mb_tags`, wider artist
coverage) unioned with Spotify genres. Matching is case-insensitive **substring**
(so `-g rock` catches "classic rock", `-g "drum and bass"` catches "liquid drum and
bass"); **exclude wins over include**. Artists with no genre data are dropped when
`-g` is used and kept when only `-x` is used. Genre data is loaded lazily (only when
a filter is active) and cached per-artist (~30d), so the unfiltered path costs
nothing extra. On `suggest` the filter runs as a free eliminator *before* the
expensive lyrics/LLM/flacfetch gates.

*Group aliases:* a single umbrella name expands to a curated family of subgenres,
so you don't have to enumerate them. Currently `electronic` → `electronic,
electronica, edm, electro, house, techno, trance, dubstep, drum and bass, jungle,
liquid funk, neurofunk, breakbeat, big beat, idm, ambient, downtempo, trip hop,
synthwave, hardstyle, happy hardcore, uk garage, …`. So **`-x electronic` excludes
all electronic music**. The map lives in `GENRE_GROUPS` (`candidates/generator.py`)
— add `rock`/`metal`/etc. there as needed. Any non-alias term is used literally.

Pipeline (cheap → expensive, so the slow/rate-limited steps only see survivors):
Last.fm top tracks (playcount order = the ranking) → **free eliminators** [reject
list · "already ours" (fresh Firestore `jobs`) · KaraokeNerds community versions]
→ **Spotify audio-features match** (mandatory; batched BigQuery + cache) → **LRCLIB
lyrics** → **karaoke-suitability score** (instrumentalness/duration/richness; cheap
pre-filter) → **LLM judge** (Gemini via Vertex; reads the lyrics + metadata — the
real quality gate, catches mostly-instrumental / wrong-lyrics / over-repetitive) →
**flacfetch high-quality-FLAC** hard gate. Rejections/misses logged to
`candidates/output/rejected_misses.csv`.

Requires (workspace `.envrc` / direnv): `ANDREW_LASTFM_APIKEY` (or `LASTFM_API_KEY`),
`FLACFETCH_API_KEY`, and GCP ADC with read access to BigQuery + Firestore and
`generate_content` on Vertex AI (the LLM judge uses ADC — no API key). Data +
caches live in `candidates/` (reject list committed; caches/reports gitignored).
Design + calibration notes:
`docs/archive/2026-08-30-karaoke-candidate-tool-v2-kickoff.md`.

## Development

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for setup instructions.

```bash
# Clone the repo
git clone https://github.com/nomadkaraoke/karaoke-decide
cd karaoke-decide

# Install dependencies
poetry install

# Run tests
make test

# Start local API server
make dev
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) - System design and data flow
- [Development](docs/DEVELOPMENT.md) - Local setup and testing
- [API Reference](docs/API.md) - Backend API documentation

## Related Projects

- [Nomad Karaoke Generator](https://github.com/nomadkaraoke/karaoke-gen) - Generate karaoke videos from any song
- [KaraokeNerds](https://karaokenerds.com) - Community karaoke catalog

## License

MIT
