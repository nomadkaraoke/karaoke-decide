"""Generate karaoke_decide/etl/musicbrainz_schema.json from musicbrainz-server sources.

The full MusicBrainz mirror (``musicbrainz`` dataset) loads every table in
``mbdump.tar.bz2`` and ``mbdump-derived.tar.bz2``. Dump files are headerless
PostgreSQL COPY text, so column names/types come from the server's
``admin/sql/CreateTables.sql`` and the archive membership from
``lib/MusicBrainz/Server/Constants.pm`` (CORE_TABLE_LIST / DERIVED_TABLE_LIST).

Re-run after a MusicBrainz schema change (SCHEMA_SEQUENCE bump), then commit:

    python scripts/gen_musicbrainz_schema.py [--ref master]
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from pathlib import Path

RAW = "https://raw.githubusercontent.com/metabrainz/musicbrainz-server/{ref}/{path}"
OUT = Path(__file__).resolve().parent.parent / "karaoke_decide" / "etl" / "musicbrainz_schema.json"
ARCHIVE_LISTS = {
    "mbdump.tar.bz2": "CORE_TABLE_LIST",
    "mbdump-derived.tar.bz2": "DERIVED_TABLE_LIST",
}
NOT_COLUMNS = {"CONSTRAINT", "CHECK", "PRIMARY", "UNIQUE", "FOREIGN", "EXCLUDE"}


def fetch(ref: str, path: str) -> str:
    with urllib.request.urlopen(RAW.format(ref=ref, path=path), timeout=60) as resp:
        return resp.read().decode()


def bq_type(pg_type: str) -> str:
    """Map a PostgreSQL column type to the BigQuery type we cast it to."""
    t = pg_type.upper()
    if "[]" in t:
        return "STRING"  # Postgres array literal, kept verbatim ("{1,2}")
    if re.match(r"^(SERIAL|BIGSERIAL|SMALLSERIAL|INTEGER|INT|SMALLINT|BIGINT)\b", t):
        return "INT64"
    if t.startswith("BOOLEAN"):
        return "BOOL"
    if t.startswith("TIMESTAMP"):
        return "TIMESTAMP"
    if t.startswith("DATE"):
        return "DATE"
    if re.match(r"^(REAL|DOUBLE|FLOAT|NUMERIC|DECIMAL)\b", t):
        return "FLOAT64"
    return "STRING"  # UUID, VARCHAR, TEXT, CHAR, JSONB, INTERVAL, POINT, enums/domains


def _strip_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.split("\n"))


def _table_bodies(create_sql: str) -> dict[str, str]:
    """{table: text between its CREATE TABLE parens}, found by paren depth."""
    sql = _strip_comments(create_sql)
    bodies: dict[str, str] = {}
    for m in re.finditer(r"^CREATE TABLE (\w+)\s*\(", sql, re.M):
        start = depth = m.end()
        depth = 1
        i = start
        while depth:
            depth += {"(": 1, ")": -1}.get(sql[i], 0)
            i += 1
        bodies[m.group(1)] = sql[start : i - 1]
    return bodies


def _split_top_level(body: str) -> list[str]:
    """Split a CREATE TABLE body on commas that aren't inside parentheses."""
    parts, depth, cur = [], 0, []
    for ch in body:
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
            continue
        depth += {"(": 1, ")": -1}.get(ch, 0)
        cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def parse_tables(create_sql: str) -> dict[str, list[list[str]]]:
    """{table: [[column, pg_type, bq_type], ...]} in declaration (= COPY) order."""
    tables: dict[str, list[list[str]]] = {}
    for name, body in _table_bodies(create_sql).items():
        cols: list[list[str]] = []
        for part in _split_top_level(body):
            words = part.split()
            if words[0].upper() in NOT_COLUMNS:
                continue
            rest = part[len(words[0]) :].strip()
            pg_type = re.split(
                r"\s+(?:NOT|NULL|DEFAULT|CHECK|REFERENCES|CONSTRAINT|PRIMARY|UNIQUE|GENERATED)\b", rest, maxsplit=1
            )[0].strip()
            cols.append([words[0], pg_type, bq_type(pg_type)])
        tables[name] = cols
    return tables


def parse_list(constants_pm: str, list_name: str) -> list[str]:
    m = re.search(rf"@{list_name} => qw\((.*?)\);", constants_pm, re.S)
    if not m:
        raise SystemExit(f"{list_name} not found in Constants.pm")
    return m.group(1).split()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--ref", default="master", help="musicbrainz-server git ref")
    args = parser.parse_args()

    create_sql = fetch(args.ref, "admin/sql/CreateTables.sql")
    constants = fetch(args.ref, "lib/MusicBrainz/Server/Constants.pm")
    dbdefs = fetch(args.ref, "lib/DBDefs/Default.pm")
    seq = re.search(r"sub (?:ACTIVE|DB)_SCHEMA_SEQUENCE \{ (\d+) \}", dbdefs)
    tables = parse_tables(create_sql)

    archives: dict[str, dict[str, list[list[str]]]] = {}
    for archive, list_name in ARCHIVE_LISTS.items():
        members = parse_list(constants, list_name)
        missing = [t for t in members if t not in tables]
        if missing:
            raise SystemExit(f"{archive}: no CREATE TABLE for {missing}")
        archives[archive] = {t: tables[t] for t in members}

    out = {
        "schema_sequence": int(seq.group(1)) if seq else None,
        "source_ref": args.ref,
        "archives": archives,
    }
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    n = sum(len(v) for v in archives.values())
    print(f"Wrote {OUT} ({n} tables, schema_sequence={out['schema_sequence']})")


if __name__ == "__main__":
    main()
