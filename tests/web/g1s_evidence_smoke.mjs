import fs from 'node:fs';
import assert from 'node:assert/strict';

const ui=fs.readFileSync('seiltanzer/web/js/g1s_evidence.js','utf8');
const management=fs.readFileSync('seiltanzer/web/js/management_ui.js','utf8');
const routes=fs.readFileSync('seiltanzer/g1_short_horizon_routes.py','utf8');

assert.match(management,/mountG1SEvidencePanel/);
assert.match(ui,/EDGE EVIDENCE · G\.1S \/ G\.1-M\.1/);
assert.match(ui,/RAW_TARGET = 1000/);
assert.match(ui,/EFFECTIVE_TARGET = 400/);
assert.match(ui,/PROBABILITY · SELECTED/);
assert.match(ui,/CALIBRATION VALUE/);
assert.match(ui,/does_best_probability_representation_beat_baselines_oos/);
assert.match(ui,/\/api\/research\/g1s\/final-report/);
assert.match(ui,/\/api\/research\/g1s\/continuous-oos/);
assert.match(ui,/\/api\/research\/g1s\/calibration-oos/);
assert.match(ui,/\/api\/research\/g1s\/evidence-materialization/);
assert.match(ui,/Production authority/i);
assert.doesNotMatch(ui,/requestAnimationFrame|tweenNumber|Math\.random/);
assert.match(routes,/materialized_evidence_report/);
assert.match(routes,/request_time_full_history_evidence_scan/);
console.log('G1S evidence UI smoke: PASS');
