"""Deterministic shallow gradient-boosted-stump challenger for G.1S.

This is intentionally small and interpretable: fixed learning rate, fixed number
of depth-1 stumps, deterministic quantile thresholds and no hyperparameter search.
It exists to test nonlinear interactions against logistic/simple baselines, never
to select or promote a production model automatically.
"""
from __future__ import annotations

import hashlib
import json
import math
import time

import numpy as np

from .g1_short_horizon_runtime import (
    ShortHorizonRuntime, HORIZONS, FEATURE_SETS,
    MODEL_REFIT_INTERVAL_SEC, MODEL_REFIT_MIN_EFFECTIVE_DELTA,
    G1S_MODEL_VERSION, G1S_PREDICTION_VERSION,
    _brier, _logloss, _json, _loads, _sha_text, _sigmoid,
)


MODEL_FAMILY = "SHALLOW_GBT_STUMPS"
GBT_CONTRACT_VERSION = "g1s-shallow-gbt-stumps-v1"
REFINEMENT_VERSION = "g1s-gbt-challenger-refinement-v1"
N_ESTIMATORS = 16
LEARNING_RATE = 0.15
THRESHOLD_QUANTILES = (0.25, 0.50, 0.75)


def _logit(p: float) -> float:
    p=max(1e-6,min(1-1e-6,float(p)))
    return math.log(p/(1-p))


def _fit_gbt(x: np.ndarray, y: np.ndarray) -> dict:
    base_rate=float(np.mean(y)) if len(y) else 0.5
    base_logit=_logit(base_rate)
    score=np.full(len(y),base_logit,dtype=float)
    stumps=[]
    for _ in range(N_ESTIMATORS):
        residual=y-_sigmoid(score)
        best=None
        for j in range(x.shape[1]):
            col=x[:,j]
            thresholds=sorted(set(float(np.quantile(col,q)) for q in THRESHOLD_QUANTILES))
            for threshold in thresholds:
                left=col<=threshold; right=~left
                if not left.any() or not right.any():
                    continue
                lv=float(np.mean(residual[left])); rv=float(np.mean(residual[right]))
                pred=np.where(left,lv,rv)
                loss=float(np.sum((residual-pred)**2))
                candidate=(loss,j,threshold,lv,rv)
                if best is None or candidate[:3] < best[:3]:
                    best=candidate
        if best is None:
            break
        _loss,j,threshold,lv,rv=best
        score += LEARNING_RATE*np.where(x[:,j]<=threshold,lv,rv)
        stumps.append({"feature_index":int(j),"threshold":float(threshold),
                       "left_value":lv,"right_value":rv})
    return {"contract_version":GBT_CONTRACT_VERSION,"base_logit":base_logit,
            "base_rate":base_rate,"learning_rate":LEARNING_RATE,
            "n_estimators_requested":N_ESTIMATORS,"stumps":stumps,
            "threshold_quantiles":list(THRESHOLD_QUANTILES),
            "hyperparameter_search":False,"deterministic":True}


def _predict_gbt(x: np.ndarray, params: dict) -> np.ndarray:
    score=np.full(len(x),float(params["base_logit"]),dtype=float)
    lr=float(params.get("learning_rate",LEARNING_RATE))
    for stump in params.get("stumps") or []:
        j=int(stump["feature_index"]); threshold=float(stump["threshold"])
        score += lr*np.where(x[:,j]<=threshold,float(stump["left_value"]),
                             float(stump["right_value"]))
    return _sigmoid(score)


def _historical_diagnostics(runtime, rows: list[dict], feature_set: str) -> dict:
    if len(rows)<30:
        return {"status":"INSUFFICIENT","historical_walk_forward":False,
                "model_family":MODEL_FAMILY}
    ordered=sorted(rows,key=lambda r:(float(r["captured_ts"]),r["observation_id"]))
    split=max(10,int(len(ordered)*0.70))
    if split>=len(ordered):
        return {"status":"INSUFFICIENT","historical_walk_forward":False,
                "model_family":MODEL_FAMILY}
    test_start=float(ordered[split]["captured_ts"])
    train=[r for r in ordered[:split] if float(r["target_ts"])<test_start]
    test=ordered[split:]
    if len(train)<20 or len(test)<10:
        return {"status":"INSUFFICIENT_AFTER_PURGE","historical_walk_forward":True,
                "train_n":len(train),"test_n":len(test),"model_family":MODEL_FAMILY}
    x_train,y_train=runtime._training_arrays(train,feature_set)
    params=_fit_gbt(x_train,y_train)
    x_test,y_test=runtime._training_arrays(test,feature_set)
    ps=[float(v) for v in _predict_gbt(x_test,params)]
    ys=[int(v) for v in y_test]
    base=float(np.mean(y_train)) if len(y_train) else 0.5
    base_ps=[base]*len(ys)
    mb=_brier(ps,ys); bb=_brier(base_ps,ys)
    return {"status":"HISTORICAL_PURGED_TEST","historical_walk_forward":True,
            "prospective_oos":False,"oos_validated":False,"random_shuffle":False,
            "purge_applied":True,"train_n":len(train),"test_n":len(test),
            "model_family":MODEL_FAMILY,"model_brier":mb,"model_log_loss":_logloss(ps,ys),
            "base_rate":base,"base_brier":bb,"base_log_loss":_logloss(base_ps,ys),
            "delta_brier_vs_base":None if mb is None or bb is None else bb-mb}


def install_g1_short_horizon_gbt_refinement():
    if getattr(ShortHorizonRuntime,"_gbt_refinement",None)==REFINEMENT_VERSION:
        return
    original_fit=ShortHorizonRuntime.fit_if_ready

    def fit(self, *, force=False):
        created=original_fit(self,force=force)
        now=time.time()
        for horizon in HORIZONS:
            rows=self._resolved_eligible(horizon); evidence=self._evidence(rows)
            if not evidence["fit_allowed"]:
                continue
            for feature_set in FEATURE_SETS:
                with self._lock:
                    latest=self._conn.execute(
                        "SELECT created_ts,effective_n FROM g1s_models WHERE horizon_minutes=? "
                        "AND feature_set=? AND model_family=? ORDER BY created_ts DESC LIMIT 1",
                        (horizon,feature_set,MODEL_FAMILY)).fetchone()
                if latest and not force:
                    if now-float(latest["created_ts"])<MODEL_REFIT_INTERVAL_SEC:
                        continue
                    if evidence["effective_n"]-float(latest["effective_n"])<MODEL_REFIT_MIN_EFFECTIVE_DELTA:
                        continue
                x,y=self._training_arrays(rows,feature_set)
                params=_fit_gbt(x,y)
                params["feature_names"]=list(FEATURE_SETS[feature_set]) + [
                    f"instrument:{code}" for code in tuple(__import__('seiltanzer.config',fromlist=['INSTRUMENTS']).INSTRUMENTS)[1:]]
                cutoff=max(float(r["resolved_ts"]) for r in rows)
                diagnostics=_historical_diagnostics(self,rows,feature_set)
                artifact={"contract_version":G1S_MODEL_VERSION,"model_subcontract":GBT_CONTRACT_VERSION,
                          "model_family":MODEL_FAMILY,"horizon_minutes":horizon,
                          "feature_set":feature_set,"training_cutoff_ts":cutoff,
                          "source_observation_ids":[r["observation_id"] for r in rows],
                          "parameters":params}
                artifact_sha=_sha_text(_json(artifact)); model_id="g1s-gbt-"+artifact_sha[:28]
                with self._lock,self._conn:
                    cur=self._conn.execute(
                        "INSERT OR IGNORE INTO g1s_models(model_id,model_family,horizon_minutes,"
                        "feature_set,training_cutoff_ts,raw_n,effective_n,positive_n,negative_n,"
                        "training_days,parameters_json,artifact_sha256,diagnostics_json,authority,created_ts)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'research_only',?)",
                        (model_id,MODEL_FAMILY,horizon,feature_set,cutoff,evidence["raw_resolved"],
                         float(evidence["effective_n"]),evidence["positive_n"],evidence["negative_n"],
                         evidence["trading_days"],_json(params),artifact_sha,_json(diagnostics),now))
                    created += int(cur.rowcount>0)
        return created

    def prospective_predictions(self, observation_id: str, captured_ts: float, horizon: int) -> int:
        with self._lock:
            obs=self._conn.execute("SELECT * FROM g1s_observations WHERE observation_id=?",
                                   (observation_id,)).fetchone()
            models=self._conn.execute(
                "SELECT * FROM g1s_models WHERE horizon_minutes=? AND created_ts<=? "
                "AND training_cutoff_ts<? ORDER BY created_ts DESC",
                (horizon,captured_ts,captured_ts)).fetchall()
        if obs is None:
            return 0
        chosen={}
        for model in models:
            chosen.setdefault((str(model["model_family"]),str(model["feature_set"])),model)
        written=0
        for model in chosen.values():
            feature_set=str(model["feature_set"])
            if feature_set not in FEATURE_SETS:
                continue
            vector,_=self._feature_vector(dict(obs),feature_set)
            x=np.asarray([vector],dtype=float); params=_loads(model["parameters_json"],{})
            family=str(model["model_family"])
            if family=="REGULARIZED_LOGISTIC":
                mean=np.asarray(params.get("feature_mean") or [],dtype=float)
                std=np.asarray(params.get("feature_std") or [],dtype=float)
                beta=np.asarray(params.get("intercept_and_coefficients") or [],dtype=float)
                row=x[0]
                if len(mean)!=len(row) or len(std)!=len(row) or len(beta)!=len(row)+1:
                    self._error("MODEL_ARTIFACT_SHAPE_MISMATCH",str(model["model_id"]),
                                observation_id=observation_id,critical=True); continue
                z=(row-mean)/np.where(std<1e-12,1.0,std)
                p_up=float(_sigmoid(np.asarray([beta[0]+z@beta[1:]]))[0])
            elif family==MODEL_FAMILY:
                try:
                    p_up=float(_predict_gbt(x,params)[0])
                except Exception as exc:
                    self._error("MODEL_ARTIFACT_SHAPE_MISMATCH",f"{model['model_id']}: {exc}",
                                observation_id=observation_id,critical=True); continue
            else:
                continue
            payload={"contract_version":G1S_PREDICTION_VERSION,"observation_id":observation_id,
                     "model_id":str(model["model_id"]),"model_family":family,
                     "feature_set":feature_set,"model_created_ts":float(model["created_ts"]),
                     "training_cutoff_ts":float(model["training_cutoff_ts"]),
                     "captured_ts":captured_ts,"p_up":p_up,"research_only":True,
                     "production_used":False}
            raw=_json(payload); pred_id="g1s-pred-"+hashlib.sha256(raw.encode()).hexdigest()[:30]
            with self._lock,self._conn:
                cur=self._conn.execute(
                    "INSERT OR IGNORE INTO g1s_shadow_predictions(prediction_id,observation_id,"
                    "model_id,created_ts,p_up,prediction_json,prediction_sha256,production_used) "
                    "VALUES(?,?,?,?,?,?,?,0)",
                    (pred_id,observation_id,model["model_id"],time.time(),p_up,raw,
                     hashlib.sha256(raw.encode()).hexdigest()))
                written += int(cur.rowcount>0)
        return written

    ShortHorizonRuntime.fit_if_ready=fit
    ShortHorizonRuntime._create_prospective_predictions=prospective_predictions
    ShortHorizonRuntime._gbt_refinement=REFINEMENT_VERSION
