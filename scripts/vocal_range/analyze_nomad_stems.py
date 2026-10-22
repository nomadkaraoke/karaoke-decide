#!/usr/bin/env python3
"""Measure vocal ranges of the NOMAD catalog's vocal stems (runs on NomadPC).

    nice -n 19 python analyze_nomad_stems.py --in /opt/nomad/downloads/NOMAD-vocals \
        --out nomad_measured.csv [--limit N]

Standalone: ``pitch.py`` (copy of karaoke_decide/vocal_range/pitch.py) must sit next
to this file; no decide checkout needed. Resumable: brands already in --out are
skipped and new rows are appended.

Filenames are ``NOMAD-#### - Artist - Title.<ext>``. Split on the first two " - "
after the brand only, so a title containing " - " stays intact. An artist that itself
contains " - " is inherently ambiguous and is split at the first separator (the rest
lands in the title); source_id is the reliable key, artist/title are informational.
Names that do not match are skipped and counted.

CSV columns: source_id,artist,title,lead_part,low_midi,high_midi,p10,p50,p90,voiced_seconds
(lead_part is empty for NOMAD: the stem is the lead vocal mix).
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from pathlib import Path

COLUMNS = [
    "source_id",
    "artist",
    "title",
    "lead_part",
    "low_midi",
    "high_midi",
    "p10",
    "p50",
    "p90",
    "voiced_seconds",
]
EXTS = {".flac", ".mp3"}
_NAME_RE = re.compile(r"^(NOMAD-\d{4}) - (.+?) - (.+)$", re.DOTALL)


def parse_name(filename: str) -> tuple[str, str, str] | None:
    """(source_id, artist, title) from a stem filename, or None if unparseable."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    m = _NAME_RE.match(stem)
    if not m:
        return None
    brand, artist, title = m.group(1), m.group(2).strip(), m.group(3).strip()
    if not artist or not title:
        return None
    return brand, artist, title


def done_ids(out: Path) -> set[str]:
    if not out.exists():
        return set()
    with out.open(newline="", encoding="utf-8") as f:
        return {row["source_id"] for row in csv.DictReader(f)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--in", dest="indir", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--limit", type=int, default=None, help="max stems to analyze this run")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import soundfile as sf
    from pitch import analyze_vocals

    done = done_ids(args.out)
    files = sorted(p for p in args.indir.iterdir() if p.suffix.lower() in EXTS)
    new_file = not args.out.exists() or args.out.stat().st_size == 0
    skipped_name = skipped_done = too_short = failed = written = 0

    with args.out.open("a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new_file:
            w.writerow(COLUMNS)
        for p in files:
            parsed = parse_name(p.name)
            if parsed is None:
                skipped_name += 1
                continue
            brand, artist, title = parsed
            if brand in done:
                skipped_done += 1
                continue
            if args.limit is not None and (written + too_short + failed) >= args.limit:
                break
            t0 = time.time()
            try:
                y, sr = sf.read(str(p), dtype="float32", always_2d=False)
                stats = analyze_vocals(y, sr)
            except Exception as e:  # keep the long run alive
                failed += 1
                print(f"FAIL {brand}: {e!r}", flush=True)
                continue
            done.add(brand)
            if stats is None:
                too_short += 1
                print(f"SHORT {brand} ({time.time() - t0:.1f}s)", flush=True)
                # Record the miss so a resume does not redo it.
                w.writerow([brand, artist, title, "", "", "", "", "", "", ""])
            else:
                written += 1
                w.writerow(
                    [
                        brand,
                        artist,
                        title,
                        "",
                        f"{stats.low:.2f}",
                        f"{stats.high:.2f}",
                        f"{stats.p10:.2f}",
                        f"{stats.p50:.2f}",
                        f"{stats.p90:.2f}",
                        f"{stats.voiced_s:.1f}",
                    ]
                )
                print(f"OK {brand} {time.time() - t0:.1f}s", flush=True)
            fh.flush()

    print(
        f"done: rows={written} too_short={too_short} failed={failed} "
        f"skipped_unparseable={skipped_name} skipped_already_done={skipped_done}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
