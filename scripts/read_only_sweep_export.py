#!/usr/bin/env python3
"""One-shot, read-only sweep-vs-breakout research export.

The source SQLite snapshot is opened with mode=ro&immutable=1 and query_only.
This script never computes a structural level and never changes source labels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import pyarrow as pa
import pyarrow.parquet as pq


STRUCTURAL_KEY = re.compile(
    r"(?:structur|key[_ -]?level|support|resistan|pivot|"
    r"distance.*level|level.*distance|level.*source|source.*level|"
    r"swing[_ -]?(?:high|low)|value[_ -]?area|(?:^|_)vwap(?:_|$)|"
    r"(?:^|_)poc(?:_|$)|zero[_ -]?gamma|(?:call|put|gamma|dealer)[_ -]?wall|"
    r"liquidity[_ -]?level)",
    re.IGNORECASE,
)
JSON_COLUMN = re.compile(r"(?:json|snapshot|context|features|forecast|outcome|resolution)$", re.I)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def iso_utc(value: Any) -> str | None:
    ts = finite(value)
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')]


def json_load(raw: Any) -> Any:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def walk(value: Any, path: str = "") -> Iterable[tuple[str, str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            child = f"{path}.{key_text}" if path else key_text
            yield child, key_text, item
            yield from walk(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            child = f"{path}[{index}]"
            yield from walk(item, child)


def raw_json_sources(row: dict[str, Any]) -> Iterable[tuple[str, Any]]:
    for name, raw in row.items():
        if isinstance(raw, str) and JSON_COLUMN.search(name):
            parsed = json_load(raw)
            if parsed is not None:
                yield name, parsed


def structural_fields(row: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for source_name, parsed in raw_json_sources(row):
        for path, key, value in walk(parsed):
            if STRUCTURAL_KEY.search(key) or STRUCTURAL_KEY.search(path):
                found[f"{source_name}:{path}"] = value
    for name, value in row.items():
        if STRUCTURAL_KEY.search(name):
            found[f"column:{name}"] = value
    return found


def first_named_json(row: dict[str, Any], names: set[str]) -> tuple[str | None, str | None]:
    for name, raw in row.items():
        source_column = name.rsplit("__", 1)[-1].lower()
        if source_column in names and isinstance(raw, str):
            return raw, name
    for source_name, parsed in raw_json_sources(row):
        for path, key, value in walk(parsed):
            if key.lower() not in names:
                continue
            if isinstance(value, str):
                parsed_value = json_load(value)
                return (value if parsed_value is not None else json.dumps(
                    value, ensure_ascii=False, separators=(",", ":"))), f"{source_name}:{path}"
            return json.dumps(value, ensure_ascii=False, separators=(",", ":")), f"{source_name}:{path}"
    return None, None


def select_sql(conn: sqlite3.Connection) -> tuple[str, list[str], dict[str, list[str]]]:
    if not table_exists(conn, "passive_market_observations"):
        raise RuntimeError("required table passive_market_observations is absent")
    source_columns: dict[str, list[str]] = {
        "passive_market_observations": columns(conn, "passive_market_observations")
    }
    select_parts = [f'p."{name}" AS "passive__{name}"' for name in source_columns["passive_market_observations"]]
    joins: list[str] = []
    if table_exists(conn, "g1s_observations"):
        source_columns["g1s_observations"] = columns(conn, "g1s_observations")
        select_parts.extend(f'g."{name}" AS "g1s__{name}"' for name in source_columns["g1s_observations"])
        joins.append("LEFT JOIN g1s_observations g ON g.source_observation_id=p.observation_id")
        if table_exists(conn, "g1s_resolutions"):
            source_columns["g1s_resolutions"] = columns(conn, "g1s_resolutions")
            select_parts.extend(f'r."{name}" AS "resolution__{name}"' for name in source_columns["g1s_resolutions"])
            joins.append("LEFT JOIN g1s_resolutions r ON r.observation_id=g.observation_id")
    sql = (
        "SELECT " + ",".join(select_parts) + " FROM passive_market_observations p "
        + " ".join(joins) + " ORDER BY p.captured_ts,p.instrument,p.observation_id"
    )
    return sql, [part.split(' AS ')[-1].strip('"') for part in select_parts], source_columns


def event_rows(conn: sqlite3.Connection, bar_coverage: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[str]], Counter[str]]:
    sql, _aliases, source_columns = select_sql(conn)
    output: list[dict[str, Any]] = []
    structural_paths: Counter[str] = Counter()
    for source in conn.execute(sql):
        raw = dict(source)
        instrument = str(raw.get("passive__instrument") or raw.get("g1s__instrument") or "")
        t0 = finite(raw.get("passive__captured_ts") or raw.get("g1s__captured_ts"))
        coverage = bar_coverage.get(instrument)
        if t0 is None or coverage is None:
            continue
        # Keep only observations that overlap retained real bars. Coverage flags
        # below let the downstream test choose its own lookback requirement.
        if t0 < float(coverage["min_bar_end_ts"]) or t0 > float(coverage["max_bar_end_ts"]):
            continue
        row = {
            "event_id": raw.get("passive__observation_id") or raw.get("g1s__source_observation_id") or raw.get("g1s__observation_id"),
            "observation_id": raw.get("passive__observation_id"),
            "g1s_observation_id": raw.get("g1s__observation_id"),
            "instrument": instrument,
            "captured_ts": t0,
            "captured_ts_utc": iso_utc(t0),
            "target_ts": finite(raw.get("passive__target_ts") or raw.get("g1s__target_ts")),
            "target_ts_utc": iso_utc(raw.get("passive__target_ts") or raw.get("g1s__target_ts")),
            "resolved_ts": finite(raw.get("passive__resolved_ts") or raw.get("resolution__resolved_ts")),
            "resolved_ts_utc": iso_utc(raw.get("passive__resolved_ts") or raw.get("resolution__resolved_ts")),
            "market_price_t0": finite(raw.get("passive__market_price") or raw.get("g1s__market_price")),
            "resolution_status": raw.get("passive__resolution_status"),
            "resolved_outcome_json": raw.get("passive__outcome_json"),
            "direction_label": raw.get("resolution__direction_label"),
            "horizon_minutes": raw.get("passive__horizon_minutes") or raw.get("g1s__horizon_minutes"),
            "bar_min_ts": coverage["min_bar_end_ts"],
            "bar_max_ts": coverage["max_bar_end_ts"],
            "available_pre_t0_seconds": max(0.0, t0 - float(coverage["min_bar_end_ts"])),
            "available_post_t0_seconds": max(0.0, float(coverage["max_bar_end_ts"]) - t0),
            "available_pre_t0_bars": int(conn.execute(
                "SELECT COUNT(*) FROM passive_market_bars WHERE instrument=? AND bar_end_ts<=?",
                (instrument, t0),
            ).fetchone()[0]),
            "available_post_t0_bars": int(conn.execute(
                "SELECT COUNT(*) FROM passive_market_bars WHERE instrument=? AND bar_start_ts>=?",
                (instrument, t0),
            ).fetchone()[0]),
        }
        row.update(raw)
        frozen, frozen_source = first_named_json(
            raw, {"frozen_context_json", "frozen_features_json", "features_json"}
        )
        critical, critical_source = first_named_json(raw, {"critical_fields_json", "critical_fields"})
        row["frozen_context_json"] = frozen
        row["frozen_context_source"] = frozen_source
        row["critical_fields_json"] = critical
        row["critical_fields_source"] = critical_source
        found = structural_fields(raw)
        row["structural_fields_json"] = json.dumps(
            found, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        row["structural_field_count"] = len(found)
        structural_paths.update(found.keys())
        output.append(row)
    return output, source_columns, structural_paths


def bar_rows(conn: sqlite3.Connection, instruments: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    if not table_exists(conn, "passive_market_bars"):
        raise RuntimeError("required table passive_market_bars is absent")
    names = columns(conn, "passive_market_bars")
    placeholders = ",".join("?" for _ in instruments)
    sql = (
        "SELECT * FROM passive_market_bars WHERE instrument IN (" + placeholders + ") "
        "ORDER BY instrument,bar_start_ts"
    )
    rows: list[dict[str, Any]] = []
    for source in conn.execute(sql, sorted(instruments)):
        row = dict(source)
        row["timestamp"] = row.get("bar_end_ts") or row.get("bar_start_ts")
        row["timestamp_utc"] = iso_utc(row["timestamp"])
        row["bar_start_ts_utc"] = iso_utc(row.get("bar_start_ts"))
        row["bar_end_ts_utc"] = iso_utc(row.get("bar_end_ts"))
        row["created_ts_utc"] = iso_utc(row.get("created_ts"))
        rows.append(row)
    return rows, names


def parquet_write(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        raise RuntimeError(f"refusing to write empty dataset: {path.name}")
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path, compression="zstd", use_dictionary=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--source-manifest")
    parser.add_argument("--source-run-id")
    args = parser.parse_args()

    db_path = Path(args.database).resolve(strict=True)
    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    uri = f"file:{quote(str(db_path), safe='/')}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    if conn.execute("PRAGMA query_only").fetchone()[0] != 1:
        raise RuntimeError("SQLite query_only guard did not arm")
    quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
    if quick_check != "ok":
        raise RuntimeError(f"source quick_check failed: {quick_check}")

    required = {"passive_market_observations", "passive_market_bars"}
    present = {str(row[0]) for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    missing = sorted(required - present)
    if missing:
        raise RuntimeError(f"required tables absent: {missing}")

    coverage: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        "SELECT instrument,MIN(bar_start_ts),MIN(bar_end_ts),MAX(bar_end_ts),COUNT(*) "
        "FROM passive_market_bars GROUP BY instrument"
    ):
        coverage[str(row[0])] = {
            "min_bar_start_ts": float(row[1]),
            "min_bar_end_ts": float(row[2]),
            "max_bar_end_ts": float(row[3]),
            "row_count": int(row[4]),
        }

    events, event_sources, structural_paths = event_rows(conn, coverage)
    instruments = {str(row["instrument"]) for row in events}
    bars, bar_source_columns = bar_rows(conn, instruments)
    conn.close()

    events_path = out / "events.parquet"
    bars_path = out / "bars.parquet"
    parquet_write(events, events_path)
    parquet_write(bars, bars_path)

    source_manifest: dict[str, Any] | None = None
    if args.source_manifest:
        source_manifest = json.loads(Path(args.source_manifest).read_text(encoding="utf-8"))
    files = {}
    for path in (events_path, bars_path):
        files[path.name] = {
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    event_ts = [float(row["captured_ts"]) for row in events]
    bar_ts = [float(row["timestamp"]) for row in bars]
    schema = {
        "contract": "sweep-breakout-read-only-export-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "database_file": db_path.name,
            "database_size_bytes": db_path.stat().st_size,
            "database_sha256": sha256_file(db_path),
            "sqlite_open_uri_flags": "mode=ro&immutable=1",
            "pragma_query_only": True,
            "pragma_quick_check": quick_check,
            "source_run_id": args.source_run_id,
            "verified_backup_id": (source_manifest or {}).get("backup_id"),
            "verified_backup_created_ts": (source_manifest or {}).get("created_ts"),
            "verified_backup_reason": (source_manifest or {}).get("reason"),
            "source_tables": event_sources | {"passive_market_bars": bar_source_columns},
        },
        "selection": {
            "events": "passive_market_observations whose T0 overlaps retained passive_market_bars for the same instrument; optional g1s_observations/g1s_resolutions joined by immutable IDs",
            "bars": "all retained passive_market_bars for exported event instruments",
            "level_computed": False,
            "labels_changed": False,
            "start_price_barriers_used_as_level": False,
            "causal_pre_t0_rule": "downstream pre-T0 features must use bar_end_ts<=captured_ts and, when provenance is required, created_ts<=event capture record created_ts",
            "post_t0_role": "post-T0 bars are outcome-path evidence only and must never enter pre-T0 features",
        },
        "tables": {
            "events": {
                "description": "Raw passive prospective T0 events, frozen JSON, unchanged outcomes/status, optional G1S immutable mirror/resolution, structural field path inventory, and bar-coverage QA metadata.",
                "row_count": len(events),
                "columns": list(events[0].keys()),
                "min_captured_ts": min(event_ts),
                "min_captured_ts_utc": iso_utc(min(event_ts)),
                "max_captured_ts": max(event_ts),
                "max_captured_ts_utc": iso_utc(max(event_ts)),
                "instruments": sorted(instruments),
                "structural_field_paths": [
                    {"path": path, "event_count": count}
                    for path, count in structural_paths.most_common()
                ],
            },
            "bars": {
                "description": "Unchanged retained passive_market_bars OHLC rows with source, quality, kind and created_ts provenance where present.",
                "row_count": len(bars),
                "columns": list(bars[0].keys()),
                "min_timestamp": min(bar_ts),
                "min_timestamp_utc": iso_utc(min(bar_ts)),
                "max_timestamp": max(bar_ts),
                "max_timestamp_utc": iso_utc(max(bar_ts)),
                "instruments": sorted({str(row["instrument"]) for row in bars}),
            },
        },
        "files": files,
    }
    schema_path = out / "schema.json"
    schema["files"][schema_path.name] = {
        "size_bytes": 0,
        "sha256": None,
        "sha256_note": "omitted because schema.json cannot contain its own stable digest",
    }
    for _ in range(5):
        schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
        actual_size = schema_path.stat().st_size
        if schema["files"][schema_path.name]["size_bytes"] == actual_size:
            break
        schema["files"][schema_path.name]["size_bytes"] = actual_size
    schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "events": len(events), "bars": len(bars),
        "instruments": sorted(instruments), "output_dir": str(out),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
