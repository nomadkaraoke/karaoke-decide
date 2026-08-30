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
that have **real singable lyrics** (LRCLIB), and that can be **sourced as a
high-quality FLAC** (flacfetch). Designed to be run by any agent to get "the next
N real karaoke tracks to make".

```bash
# The next 5 songs worth making (pure Last.fm-playcount order among survivors)
karaoke-decide candidates suggest --count 5

# JSON output, ready to feed into the gen create-job flow
karaoke-decide candidates suggest --count 5 --format json

# Tune the lyric-richness gate against real data before trusting it
karaoke-decide candidates calibrate --sample 200

# Mark a song you've decided not to make (never suggested again)
karaoke-decide candidates reject "Pendulum" "Slam" --reason "too repetitive live"
karaoke-decide candidates review-rejects
```

Pipeline (cheap → expensive, so the rate-limited APIs only see survivors):
Last.fm top tracks → reject list + "already ours" (fresh Firestore `jobs`) +
KaraokeNerds community versions → LRCLIB lyrics + richness heuristic → flacfetch
high-quality-FLAC hard gate (misses logged to `candidates/output/`).

Requires (workspace `.envrc` / direnv): `ANDREW_LASTFM_APIKEY` (or `LASTFM_API_KEY`),
`FLACFETCH_API_KEY`, and read-only GCP ADC for BigQuery + Firestore. Data + caches
live in `candidates/` (reject list committed; caches/reports gitignored). See
`docs/archive/2026-08-30-karaoke-candidate-tool-v2-kickoff.md` for the full design.

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
