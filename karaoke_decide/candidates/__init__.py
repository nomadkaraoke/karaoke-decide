"""Karaoke-job candidate generation.

Turns Andrew's Last.fm listening history into a ranked, gated list of songs
worth producing as Nomad karaoke jobs: songs he plays a lot, that have no
existing community/Nomad karaoke version, that have real singable lyrics
(LRCLIB), and that can actually be sourced as a high-quality FLAC (flacfetch).

See docs/archive/2026-08-30-karaoke-candidate-tool-v2-kickoff.md for the full
spec and design.
"""
