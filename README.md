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
