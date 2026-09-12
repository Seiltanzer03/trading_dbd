"""One-shot full cloud restore drill and read-only hypothesis verification."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import zlib

import boto3
from botocore.config import Config

from production_ede_v13_audit import ReadOnlyRuntime
from seiltanzer import llm_edge_evaluator as evaluator
from seiltanzer.edge_discovery.filters import fit_rule, rule_mask
from seiltanzer.edge_discovery.universal_structured_discovery import (
    _effective_n,
    _sample_allowed,
)
from seiltanzer.edge_discovery.universal_target_scoring import eligible_target_rows
from seiltanzer.g1_short_horizon_historical_wf import _historical_folds


def _resolution_quality(runtime):
    rows = runtime._conn.execute(
        "SELECT COALESCE(path_quality_status,'NULL') quality,COUNT(*) total,"
        "SUM(mfe_log_return IS NOT NULL) mfe_nonnull,"
        "SUM(mae_log_return IS NOT NULL) mae_nonnull,"
        "SUM(terminal_log_return IS NOT NULL) terminal_nonnull "
        "FROM g1s_resolutions GROUP BY COALESCE(path_quality_status,'NULL') "
        "ORDER BY total DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def _fold_diagnostics(rows, hypothesis, specs):
    horizon = int(hypothesis["horizon_minutes"])
    spec = specs.get(str(hypothesis["target_id"]))
    if spec is None:
        return []
    target_rows = eligible_target_rows(
        [row for row in rows if int(row["horizon_minutes"]) == horizon], spec)
    template = evaluator._template(hypothesis)
    output = []
    for fold in _historical_folds(target_rows, horizon):
        rule = fit_rule(template, fold["train"])
        item = {
            "fold_index": fold["fold_index"],
            "train_raw": len(fold["train"]),
            "test_raw": len(fold["test"]),
            "rule_fit": rule is not None,
        }
        if rule is not None:
            selected_train = [
                row for row, keep in zip(fold["train"], rule_mask(fold["train"], rule))
                if keep
            ]
            selected_test = [
                row for row, keep in zip(fold["test"], rule_mask(fold["test"], rule))
                if keep
            ]
            item.update({
                "selected_train_raw": len(selected_train),
                "selected_train_effective": _effective_n(selected_train),
                "selected_test_raw": len(selected_test),
                "selected_test_effective": _effective_n(selected_test),
                "sample_gate_passed": _sample_allowed(selected_train, selected_test, spec),
            })
        output.append(item)
    return output


def main():
    bucket = "trading-dbd-backups-2026"
    key = os.environ["BACKUP_KEY"]
    destination = Path(os.environ["RUNNER_TEMP"]) / "restored.sqlite3"
    report_path = Path(os.environ["RUNNER_TEMP"]) / "verified-research.json"
    client = boto3.client(
        "s3",
        endpoint_url="https://storage.yandexcloud.net",
        region_name="ru-central1",
        config=Config(connect_timeout=10, read_timeout=120),
    )
    manifest = json.loads(
        client.get_object(Bucket=bucket, Key=key + ".manifest.json")["Body"].read())
    assert manifest["git_commit"] == os.environ["EXPECTED_SHA"]
    assert manifest["object_key"] == key and manifest["bucket"] == bucket
    assert not destination.exists()
    assert shutil.disk_usage(destination.parent).free > manifest["database_size_bytes"] + 1024**3
    compressed_hash, raw_hash = hashlib.sha256(), hashlib.sha256()
    compressed_size = raw_size = 0
    decoder = zlib.decompressobj(31)
    body = client.get_object(Bucket=bucket, Key=key)["Body"]
    with destination.open("xb") as output:
        for part in body.iter_chunks(chunk_size=64 * 1024):
            compressed_hash.update(part)
            compressed_size += len(part)
            raw = decoder.decompress(part)
            raw_size += len(raw)
            assert raw_size <= manifest["database_size_bytes"]
            raw_hash.update(raw)
            output.write(raw)
        tail = decoder.flush()
        raw_hash.update(tail)
        raw_size += len(tail)
        output.write(tail)
    body.close()
    assert decoder.eof and not decoder.unused_data
    assert compressed_size == manifest["compressed_size_bytes"]
    assert compressed_hash.hexdigest() == manifest["compressed_sha256"]
    assert raw_size == manifest["database_size_bytes"]
    assert raw_hash.hexdigest() == manifest["database_sha256"]

    runtime = ReadOnlyRuntime(destination)
    assert runtime._conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    print("FULL_CLOUD_RESTORE_VERIFIED", raw_size, compressed_size, flush=True)
    quality = _resolution_quality(runtime)
    for item in quality:
        print("RESOLUTION_QUALITY " + json.dumps(item, sort_keys=True), flush=True)
    report = {
        "source_manifest": manifest,
        "full_cloud_restore_verified": True,
        "resolution_quality": quality,
        "runs": [],
    }
    runs = runtime._conn.execute(
        "SELECT * FROM llm_edge_research_runs WHERE hypothesis_ids_json != '[]' "
        "ORDER BY created_ts DESC LIMIT 6"
    ).fetchall()
    for run in runs:
        ids = json.loads(run["hypothesis_ids_json"])
        hypotheses = evaluator._load_hypotheses(runtime, ids)
        cutoff = float(run["created_ts"])
        rows = evaluator._resolved_rows_at_cutoff(
            runtime, cutoff, {h["horizon_minutes"] for h in hypotheses})
        specs = evaluator._specs(rows)
        results = [
            evaluator._evaluate_one(rows, h, cutoff_ts=cutoff, specs=specs)
            for h in hypotheses
        ]
        evaluator._apply_multiple_testing(results)
        diagnostics = {
            h["hypothesis_id"]: _fold_diagnostics(rows, h, specs)
            for h in hypotheses
        }
        report["runs"].append({
            "run_id": run["run_id"],
            "cutoff_ts": cutoff,
            "hypothesis_ids": ids,
            "results": results,
            "fold_diagnostics": diagnostics,
        })
        report_path.write_text(json.dumps(report, sort_keys=True, allow_nan=False))
        for result in results:
            summary = {key: result.get(key) for key in (
                "hypothesis_id", "target_id", "raw_rows", "target_rows",
                "fold_count", "status", "reason",
            )}
            print("HYPOTHESIS_RESULT " + json.dumps(summary, sort_keys=True), flush=True)
            for fold in diagnostics.get(result["hypothesis_id"], []):
                detail = {"hypothesis_id": result["hypothesis_id"], **fold}
                print("FOLD_DIAGNOSTIC " + json.dumps(detail, sort_keys=True), flush=True)
        del rows
    runtime.close()


if __name__ == "__main__":
    main()
