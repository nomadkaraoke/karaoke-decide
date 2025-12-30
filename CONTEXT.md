# Nomad Karaoke Decide - Project Context

## Background

The original roadmap document (`KaraokeHunt-original-roadmap.md`) is from February 2023, when I first decided to take my 10+ year old idea of building "Karaokay" (a karaoke song chooser app) and try to actually build it.

I ended up making it with FlutterFlow as a mobile app called KaraokeHunt, which was fun and exciting to release to the app stores, but ultimately didn't have much functionality and definitely didn't deliver on the vision of a tool to help you choose songs to sing at karaoke.

**Current state of KaraokeHunt:** It currently doesn't really add any value - it just lists and allows simple search of songs from karaokenerds.com with no other functionality. I'm ashamed it's even still live in this useless form, but basically I got overwhelmed with where to start with building the rest of it and never made progress on the backend / core functionality.

## Related Project: Nomad Karaoke Generator

Since then, I also got the spark to build out the **Nomad Karaoke Generator**, a software product under my existing Nomad Karaoke brand to allow anyone to generate karaoke videos on demand. I've made tons of progress on that, it's almost live and usable:

- GitHub: https://github.com/nomadkaraoke/karaoke-gen
- Live: https://gen.nomadkaraoke.com

## New Direction: Nomad Karaoke Decide

I'd like to reboot this song chooser app but forget about the mobile app for now and start from scratch in a more achievable and realistic way:

1. **Use Claude Code** to build this out as a new product
2. **Backend API and CLI first** - get core functionality working before any UI
3. **Realistic MLP** (Minimum Lovable Product) set of features implemented, working, and thoroughly tested
4. **Cloud backend on GCP** - following patterns from karaoke-gen
5. **Static web frontend** - build this after the backend is solid

### Branding

- **Product name:** Nomad Karaoke Decide
- **Domain:** decide.nomadkaraoke.com
- **GitHub org:** nomadkaraoke

### Development Approach

Reuse patterns, infrastructure, docs, claude skills/slash commands etc. from the karaoke-gen project to move fast on this new project.
