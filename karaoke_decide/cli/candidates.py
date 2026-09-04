"""CLI: karaoke-job candidate generation.

karaoke-decide candidates suggest --count 5
karaoke-decide candidates reject "Artist" "Title" --reason "..."
karaoke-decide candidates review-rejects
"""

from __future__ import annotations

import asyncio
import datetime
import os
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from karaoke_decide.candidates.generator import CandidateGenerator
from karaoke_decide.candidates.rejects import RejectList
from karaoke_decide.core.config import get_settings
from karaoke_decide.services.bigquery_catalog import BigQueryCatalogService
from karaoke_decide.services.flacfetch import FlacfetchClient
from karaoke_decide.services.gen_jobs import GenJobsService
from karaoke_decide.services.lastfm import LastFmClient
from karaoke_decide.services.llm_judge import LlmJudge
from karaoke_decide.services.lrclib import LrclibClient
from karaoke_decide.services.spotify_features import SpotifyFeaturesService

console = Console()


def _base_dir() -> Path:
    """Data dir holding rejects.jsonl + cache/output.

    Defaults to the repo-root ``candidates/`` directory; override with the
    ``CANDIDATES_DIR`` env var (used by tests and one-off agent runs).
    """
    override = os.environ.get("CANDIDATES_DIR")
    if override:
        return Path(override)
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "candidates"


def _build_generator(min_score: float = 45.0) -> CandidateGenerator:
    settings = get_settings()
    if not settings.lastfm_api_key:
        raise click.ClickException(
            "Last.fm API key not set. Run `direnv allow` in the workspace root "
            "(ANDREW_LASTFM_APIKEY) or set LASTFM_API_KEY."
        )
    if not settings.lastfm_username:
        raise click.ClickException(
            "Last.fm username not set. Set LASTFM_USERNAME (or the workspace's "
            "ANDREW_LASTFM_USERNAME) to the account whose history to mine."
        )
    return CandidateGenerator(
        base_dir=_base_dir(),
        lastfm=LastFmClient(settings),
        lrclib=LrclibClient(settings.lrclib_user_agent),
        flacfetch=FlacfetchClient(settings.flacfetch_api_url, settings.flacfetch_api_key),
        gen_jobs=GenJobsService(),
        catalog=BigQueryCatalogService(),
        spotify=SpotifyFeaturesService(),
        llm=LlmJudge(
            settings.vertex_project,
            settings.vertex_location,
            settings.candidates_llm_model,
        ),
        username=settings.lastfm_username,
        min_score=min_score,
    )


@click.group()
def candidates() -> None:
    """Find good karaoke songs to make from Andrew's Last.fm history."""


@candidates.command()
@click.option("--count", "-n", default=5, help="Number of picks to return")
@click.option("--min-plays", "-p", default=6, help="Minimum Last.fm playcount")
@click.option("--max-checks", default=120, help="Max songs to deep-check this run")
@click.option("--min-score", default=45.0, help="Min karaoke-suitability score (0-100)")
@click.option("--refresh-lastfm", is_flag=True, help="Re-pull Last.fm top tracks")
@click.option("--refresh-catalog", is_flag=True, help="Re-pull KaraokeNerds dump")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "md"]),
    default="table",
)
def suggest(
    count: int,
    min_plays: int,
    max_checks: int,
    min_score: float,
    refresh_lastfm: bool,
    refresh_catalog: bool,
    output_format: str,
) -> None:
    """Return N songs worth producing as karaoke jobs."""
    gen = _build_generator(min_score=min_score)

    def _tick(cand: object) -> None:
        console.print(f"  [green]✓[/green] {cand.artist} — {cand.title}")  # type: ignore[attr-defined]

    with console.status("Finding candidates (filters → Spotify → LRCLIB → score → LLM → flacfetch)..."):
        result = asyncio.run(
            gen.suggest(
                count=count,
                min_plays=min_plays,
                max_checks=max_checks,
                refresh_lastfm=refresh_lastfm,
                refresh_catalog=refresh_catalog,
                progress=None if output_format != "table" else _tick,
            )
        )
    paths = gen.write_reports(result)

    if output_format == "json":
        console.print_json(data=[c.submit_line() for c in result.confirmed])
        return
    if output_format == "md":
        console.print(paths["md"].read_text())
        return

    table = Table(title=f"Karaoke Candidates (top {len(result.confirmed)})")
    table.add_column("#", style="dim")
    table.add_column("Plays", justify="right", style="magenta")
    table.add_column("Artist", style="cyan")
    table.add_column("Title", style="green")
    table.add_column("Score", justify="right")
    table.add_column("Inst", justify="right")
    table.add_column("Dur", justify="right")
    table.add_column("FLAC", style="yellow")
    for i, c in enumerate(result.confirmed, 1):
        flac = f"{c.flac.get('provider')} {c.flac.get('seeders')}s"
        table.add_row(
            str(i),
            str(c.playcount),
            c.artist,
            c.title,
            f"{c.score:.0f}",
            f"{c.features.instrumentalness:.2f}",
            f"{c.features.duration_min:.1f}m",
            flac,
        )
    console.print(table)
    console.print(
        f"[dim]considered {result.considered} · "
        f"rejected/unsourceable {len(result.misses)} · "
        f"skipped {dict(result.skipped)}[/dim]"
    )
    console.print(f"[dim]reports → {paths['csv'].parent}[/dim]")


@candidates.command()
@click.option("--count", "-n", default=50, help="Max songs to list")
@click.option("--min-plays", "-p", default=6, help="Minimum Last.fm playcount")
@click.option("--refresh-lastfm", is_flag=True, help="Re-pull Last.fm top tracks")
@click.option("--refresh-community", is_flag=True, help="Re-pull KaraokeNerds community catalog")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "md"]),
    default="table",
)
def singable(
    count: int,
    min_plays: int,
    refresh_lastfm: bool,
    refresh_community: bool,
    output_format: str,
) -> None:
    """List your most-played songs that ALREADY have a community karaoke version."""
    gen = _build_generator()

    def _tick(song: object) -> None:
        console.print(f"  [green]✓[/green] {song.artist} — {song.title}")  # type: ignore[attr-defined]

    with console.status("Finding singable songs (Last.fm × KaraokeNerds community catalog)..."):
        result = asyncio.run(
            gen.singable(
                count=count,
                min_plays=min_plays,
                refresh_lastfm=refresh_lastfm,
                refresh_community=refresh_community,
                progress=None if output_format != "table" else _tick,
            )
        )
    paths = gen.write_singable_reports(result)

    if output_format == "json":
        console.print_json(data=[s.as_row() for s in result.songs])
        return
    if output_format == "md":
        console.print(paths["md"].read_text())
        return

    table = Table(title=f"Singable — already have community versions (top {len(result.songs)})")
    table.add_column("#", style="dim")
    table.add_column("Plays", justify="right", style="magenta")
    table.add_column("Artist", style="cyan")
    table.add_column("Title", style="green")
    table.add_column("Brands", style="yellow")
    table.add_column("Vers", justify="right")
    table.add_column("Watch", style="blue")
    for i, s in enumerate(result.songs, 1):
        table.add_row(
            str(i),
            str(s.playcount),
            s.artist,
            s.title,
            ", ".join(s.brands),
            str(s.version_count),
            s.watch or "",
        )
    console.print(table)
    console.print(
        f"[dim]considered {result.considered} played tracks · "
        f"{result.matched} have a community version[/dim]"
    )
    console.print(f"[dim]reports → {paths['csv'].parent}[/dim]")


@candidates.command()
@click.argument("artist")
@click.argument("title")
@click.option("--reason", "-r", required=True, help="Why it's not a good candidate")
def reject(artist: str, title: str, reason: str) -> None:
    """Mark a song as rejected so it's never suggested again."""
    rl = RejectList(_base_dir() / "rejects.jsonl")
    today = datetime.date.today().isoformat()
    rl.add(artist, title, reason, today)
    console.print(f"[green]Rejected[/green] {artist} — {title}  ([dim]{reason}[/dim])")


@candidates.command(name="review-rejects")
def review_rejects() -> None:
    """List rejected songs + reasons (review to improve the heuristics)."""
    rl = RejectList(_base_dir() / "rejects.jsonl")
    entries = rl.load()
    if not entries:
        console.print("[yellow]No rejects yet.[/yellow]")
        return
    table = Table(title=f"Rejected songs ({len(entries)})")
    table.add_column("Date", style="dim")
    table.add_column("Artist", style="cyan")
    table.add_column("Title", style="green")
    table.add_column("Reason")
    for e in sorted(entries, key=lambda x: x.date, reverse=True):
        table.add_row(e.date, e.artist, e.title, e.reason)
    console.print(table)
