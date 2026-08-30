"""CLI: karaoke-job candidate generation.

    karaoke-decide candidates suggest --count 5
    karaoke-decide candidates calibrate --sample 200
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
from karaoke_decide.candidates.lyrics import RichnessThresholds, is_rich
from karaoke_decide.candidates.rejects import RejectList
from karaoke_decide.core.config import get_settings
from karaoke_decide.services.bigquery_catalog import BigQueryCatalogService
from karaoke_decide.services.flacfetch import FlacfetchClient
from karaoke_decide.services.gen_jobs import GenJobsService
from karaoke_decide.services.lastfm import LastFmClient
from karaoke_decide.services.lrclib import LrclibClient

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


def _build_generator(
    thresholds: RichnessThresholds | None = None,
    min_seeders: int = 1,
    min_match_score: float = 0.8,
) -> CandidateGenerator:
    settings = get_settings()
    if not settings.lastfm_api_key:
        raise click.ClickException(
            "Last.fm API key not set. Run `direnv allow` in the workspace root "
            "(ANDREW_LASTFM_APIKEY) or set LASTFM_API_KEY."
        )
    return CandidateGenerator(
        base_dir=_base_dir(),
        lastfm=LastFmClient(settings),
        lrclib=LrclibClient(settings.lrclib_user_agent),
        flacfetch=FlacfetchClient(
            settings.flacfetch_api_url, settings.flacfetch_api_key
        ),
        gen_jobs=GenJobsService(),
        catalog=BigQueryCatalogService(),
        username=settings.lastfm_username,
        thresholds=thresholds,
        min_seeders=min_seeders,
        min_match_score=min_match_score,
    )


@click.group()
def candidates() -> None:
    """Find good karaoke songs to make from Andrew's Last.fm history."""


@candidates.command()
@click.option("--count", "-n", default=5, help="Number of picks to return")
@click.option("--min-plays", "-p", default=6, help="Minimum Last.fm playcount")
@click.option("--max-checks", default=120, help="Max songs to deep-check this run")
@click.option("--min-seeders", default=1, help="Min seeders for a FLAC to qualify")
@click.option("--min-match-score", default=0.8, help="Min flacfetch match score")
@click.option("--min-unique-lines", default=10, help="Richness gate (non-electronic)")
@click.option("--min-unique-words", default=30, help="Richness gate (non-electronic)")
@click.option(
    "--electronic-min-unique-lines", default=12,
    help="Stricter richness gate for electronic artists",
)
@click.option(
    "--electronic-min-ratio", default=0.40,
    help="Min unique-line ratio for electronic artists (anti-repetition)",
)
@click.option("--refresh-lastfm", is_flag=True, help="Re-pull Last.fm top tracks")
@click.option("--refresh-catalog", is_flag=True, help="Re-pull KaraokeNerds dump")
@click.option(
    "--format", "output_format",
    type=click.Choice(["table", "json", "md"]), default="table",
)
def suggest(
    count: int,
    min_plays: int,
    max_checks: int,
    min_seeders: int,
    min_match_score: float,
    min_unique_lines: int,
    min_unique_words: int,
    electronic_min_unique_lines: int,
    electronic_min_ratio: float,
    refresh_lastfm: bool,
    refresh_catalog: bool,
    output_format: str,
) -> None:
    """Return N songs worth producing as karaoke jobs."""
    thresholds = RichnessThresholds(
        min_unique_lines=min_unique_lines,
        min_unique_words=min_unique_words,
        electronic_min_unique_lines=electronic_min_unique_lines,
        electronic_min_unique_line_ratio=electronic_min_ratio,
    )
    gen = _build_generator(
        thresholds=thresholds,
        min_seeders=min_seeders,
        min_match_score=min_match_score,
    )

    def _tick(cand: object) -> None:
        console.print(f"  [green]✓[/green] {cand.artist} — {cand.title}")  # type: ignore[attr-defined]

    with console.status("Finding candidates (cheap filters → LRCLIB → flacfetch)..."):
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
        console.print_json(
            data=[c.submit_line() for c in result.confirmed]
        )
        return
    if output_format == "md":
        console.print(paths["md"].read_text())
        return

    table = Table(title=f"Karaoke Candidates (top {len(result.confirmed)})")
    table.add_column("#", style="dim")
    table.add_column("Plays", justify="right", style="magenta")
    table.add_column("Artist", style="cyan")
    table.add_column("Title", style="green")
    table.add_column("Uniq lines", justify="right")
    table.add_column("FLAC", style="yellow")
    for i, c in enumerate(result.confirmed, 1):
        flac = f"{c.flac.get('provider')} {c.flac.get('seeders')}s"
        table.add_row(
            str(i), str(c.playcount), c.artist, c.title,
            str(c.stats.unique_lines), flac,
        )
    console.print(table)
    console.print(
        f"[dim]considered {result.considered} · no-karaoke {result.no_karaoke} · "
        f"unsourceable {len(result.misses)} · skipped {dict(result.skipped)}[/dim]"
    )
    console.print(f"[dim]reports → {paths['csv'].parent}[/dim]")


@candidates.command()
@click.option("--sample", "-s", default=200, help="How many songs to sample")
@click.option("--min-plays", "-p", default=6, help="Minimum Last.fm playcount")
def calibrate(sample: int, min_plays: int) -> None:
    """Sample the library's lyrics and print richness distributions for tuning."""
    gen = _build_generator()
    with console.status(f"Sampling up to {sample} no-karaoke songs from LRCLIB..."):
        rows = asyncio.run(gen.calibrate(sample=sample, min_plays=min_plays))

    thresholds = RichnessThresholds()
    with_lyrics = [r for r in rows if r["has_lyrics"]]
    no_lyrics = [r for r in rows if not r["has_lyrics"]]

    console.print(
        f"\n[bold]Sampled {len(rows)}[/bold] songs · "
        f"{len(with_lyrics)} with LRCLIB lyrics · "
        f"{len(no_lyrics)} without (would be dropped)\n"
    )

    def _dist(label: str, subset: list[dict]) -> None:
        vals = sorted(r["stats"].unique_lines for r in subset)
        if not vals:
            console.print(f"[dim]{label}: no samples[/dim]")
            return
        n = len(vals)
        p = lambda q: vals[min(n - 1, int(q * n))]  # noqa: E731
        console.print(
            f"[bold]{label}[/bold] (n={n}) unique-lines  "
            f"min={vals[0]} p25={p(.25)} median={p(.5)} p75={p(.75)} max={vals[-1]}"
        )

    _dist("All with lyrics", with_lyrics)
    _dist("Electronic", [r for r in with_lyrics if r["electronic"]])
    _dist("Non-electronic", [r for r in with_lyrics if not r["electronic"]])

    # Show what the current default gate would do.
    passed = sum(
        1 for r in with_lyrics
        if is_rich(r["stats"], thresholds, r["electronic"])[0]
    )
    console.print(
        f"\nCurrent default gate would PASS [green]{passed}[/green]/"
        f"{len(with_lyrics)} songs-with-lyrics "
        f"(thresholds: {thresholds}).\n"
    )

    table = Table(title="Sample (lowest unique-line counts first — inspect the gate)")
    table.add_column("Plays", justify="right")
    table.add_column("Artist", style="cyan")
    table.add_column("Title", style="green")
    table.add_column("Elec", justify="center")
    table.add_column("U.lines", justify="right")
    table.add_column("U.words", justify="right")
    table.add_column("Gate", justify="center")
    ranked = sorted(with_lyrics, key=lambda r: r["stats"].unique_lines)
    for r in ranked[:40]:
        ok = is_rich(r["stats"], thresholds, r["electronic"])[0]
        table.add_row(
            str(r["playcount"]), r["artist"], r["title"],
            "⚡" if r["electronic"] else "",
            str(r["stats"].unique_lines), str(r["stats"].unique_words),
            "[green]✓[/green]" if ok else "[red]✗[/red]",
        )
    console.print(table)


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
