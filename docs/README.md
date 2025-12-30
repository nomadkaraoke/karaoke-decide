# Nomad Karaoke Decide Documentation

## Current Status

**Phase:** Initial Setup
**Version:** 0.1.0

### Implemented
- [x] Project structure
- [x] Core data models
- [x] CLI skeleton with command groups
- [x] FastAPI backend skeleton
- [x] Health endpoint
- [x] Text normalization utilities

### In Progress
- [ ] Firestore integration
- [ ] Magic link authentication

### Planned
- [ ] Spotify OAuth integration
- [ ] Last.fm integration
- [ ] KaraokeNerds catalog sync
- [ ] Song matching algorithm
- [ ] Playlist management

## Quick Links

| Document | Description |
|----------|-------------|
| [Architecture](ARCHITECTURE.md) | System design and data flow |
| [Development](DEVELOPMENT.md) | Local setup and testing |
| [API Reference](API.md) | Backend endpoint documentation |

## Getting Started

```bash
# Clone and install
git clone https://github.com/nomadkaraoke/karaoke-decide
cd karaoke-decide
poetry install

# Run tests
make test

# Start API server
make dev

# Run CLI
poetry run karaoke-decide --help
```
