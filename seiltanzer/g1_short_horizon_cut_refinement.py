"""Freeze exact source manifests for every G.1S shadow fit."""
from __future__ import annotations

import hashlib
import json
import time

from .g1_short_horizon_runtime import ShortHorizonRuntime, HORIZONS, G1S_MODEL_VERSION


CUT_CONTRACT_VERSION = "g1s-training-cut-v1"
REFINEMENT_VERSION = "g1s-training-cut-refinement-v1"


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _ensure(runtime):
    with runtime._lock, runtime._conn:
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_training_cuts(
                cut_id TEXT PRIMARY KEY,
                contract_version TEXT NOT NULL,
                horizon_minutes INTEGER NOT NULL,
                cutoff_ts REAL NOT NULL,
                source_manifest_json TEXT NOT NULL,
                source_manifest_sha256 TEXT NOT NULL,
                raw_n INTEGER NOT NULL,
                effective_n REAL NOT NULL,
                created_ts REAL NOT NULL)""")
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_model_cut_links(
                model_id TEXT PRIMARY KEY,
                cut_id TEXT NOT NULL,
                model_artifact_sha256 TEXT NOT NULL,
                link_json TEXT NOT NULL,
                created_ts REAL NOT NULL)""")
        for table in ("g1s_training_cuts","g1s_model_cut_links"):
            runtime._conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_immutable_update
                BEFORE UPDATE ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable G1S cut row'); END""")
            runtime._conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_immutable_delete
                BEFORE DELETE ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable G1S cut row'); END""")


def _manifest(runtime, horizon):
    with runtime._lock:
        rows=runtime._conn.execute("""
            SELECT g.observation_id,g.t0_sha256,r.resolution_sha256,g.trade_id
            FROM g1s_observations g JOIN g1s_resolutions r USING(observation_id)
            WHERE g.training_eligible=1 AND r.direction_label!='FLAT'
              AND g.horizon_minutes=? ORDER BY g.captured_ts,g.observation_id
        """, (int(horizon),)).fetchall()
    items=[{"observation_id":r["observation_id"],"t0_sha256":r["t0_sha256"],
            "resolution_sha256":r["resolution_sha256"]} for r in rows]
    return items


def install_g1_short_horizon_cut_refinement():
    if getattr(ShortHorizonRuntime,"_cut_refinement",None)==REFINEMENT_VERSION:
        return
    original_ensure=ShortHorizonRuntime._ensure_tables
    def ensure(self):
        original_ensure(self); _ensure(self)
    ShortHorizonRuntime._ensure_tables=ensure

    original_fit=ShortHorizonRuntime.fit_if_ready
    def fit(self, *, force=False):
        _ensure(self)
        snapshots={h:_manifest(self,h) for h in HORIZONS}
        with self._lock:
            before={r[0] for r in self._conn.execute("SELECT model_id FROM g1s_models").fetchall()}
        created=original_fit(self,force=force)
        if created<=0:
            return created
        with self._lock:
            models=self._conn.execute(
                "SELECT model_id,horizon_minutes,training_cutoff_ts,raw_n,effective_n,"
                "artifact_sha256 FROM g1s_models ORDER BY created_ts").fetchall()
        for model in models:
            if model["model_id"] in before:
                continue
            manifest=snapshots.get(int(model["horizon_minutes"]),[])
            manifest_raw=_json({"contract_version":CUT_CONTRACT_VERSION,
                                "horizon_minutes":int(model["horizon_minutes"]),
                                "sources":manifest})
            manifest_sha=hashlib.sha256(manifest_raw.encode()).hexdigest()
            cut_id="g1s-cut-"+manifest_sha[:28]
            link={"model_id":model["model_id"],"cut_id":cut_id,
                  "model_artifact_sha256":model["artifact_sha256"],
                  "model_contract_version":G1S_MODEL_VERSION}
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT OR IGNORE INTO g1s_training_cuts(cut_id,contract_version,"
                    "horizon_minutes,cutoff_ts,source_manifest_json,source_manifest_sha256,"
                    "raw_n,effective_n,created_ts) VALUES(?,?,?,?,?,?,?,?,?)",
                    (cut_id,CUT_CONTRACT_VERSION,int(model["horizon_minutes"]),
                     float(model["training_cutoff_ts"]),manifest_raw,manifest_sha,
                     int(model["raw_n"]),float(model["effective_n"]),time.time()))
                self._conn.execute(
                    "INSERT OR IGNORE INTO g1s_model_cut_links(model_id,cut_id,"
                    "model_artifact_sha256,link_json,created_ts) VALUES(?,?,?,?,?)",
                    (model["model_id"],cut_id,model["artifact_sha256"],_json(link),time.time()))
        return created
    ShortHorizonRuntime.fit_if_ready=fit

    def cuts(self, limit=100):
        _ensure(self)
        with self._lock:
            rows=self._conn.execute("""
                SELECT c.cut_id,c.horizon_minutes,c.cutoff_ts,c.raw_n,c.effective_n,
                       c.source_manifest_sha256,l.model_id,l.model_artifact_sha256,c.created_ts
                FROM g1s_training_cuts c LEFT JOIN g1s_model_cut_links l USING(cut_id)
                ORDER BY c.created_ts DESC LIMIT ?""", (max(1,min(int(limit),500)),)).fetchall()
        return {"contract_version":CUT_CONTRACT_VERSION,"items":[dict(r) for r in rows],
                "immutable":True,"retrospective_membership_reconstruction":False}
    ShortHorizonRuntime.cuts=cuts
    ShortHorizonRuntime._cut_refinement=REFINEMENT_VERSION
