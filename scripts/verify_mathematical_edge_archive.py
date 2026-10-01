#!/usr/bin/env python3
"""Reproduce archive hashes, the 60-row Phi_G selector and all FX H2 paths.

Optional offline audit: requires pandas/pyarrow and the user's extracted raw
snapshot. It is never executed in the terminal's HTTP or deployment process.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import numpy as np
from seiltanzer.mathematical_edge_archive import PHI_G_MEAN, PHI_G_SD, replay_frozen_fx_rule


def main():
    import pandas as pd
    parser=argparse.ArgumentParser()
    parser.add_argument('--raw-dir', required=True)
    parser.add_argument('--output', required=True)
    args=parser.parse_args(); root=Path(args.raw_dir)
    expected={'events.parquet':'9118e5bc13fee0d5515f4fb13efca5b86c03a4d5933df9f9e4bd276c1b83501d',
              'bars.parquet':'8a3b5e8ef40644bbfd0723118bc7b837011da57a05558b41e720d892be486471'}
    for name,digest in expected.items():
        if hashlib.sha256((root/name).read_bytes()).hexdigest()!=digest:
            raise ValueError('raw file hash mismatch: '+name)
    d=pd.read_parquet(root/'events.parquet')
    subset=d[(d.horizon_minutes==15)&d.direction_label.isin(['UP','DOWN']) &
             (d.captured_ts_utc.str[:10]=='2026-08-27')]
    values,ids=[],[]
    for _,row in subset.iterrows():
        try:
            frozen=json.loads(row['passive__features_json'])
            g=frozen['g1s_evidence_v3']['gex']['dynamics']
            x=[frozen['g1s_evidence_v2']['option_context']['gex_net_balance']]+[
                g[k]['slope'] for k in ('field_score','force_score','stiffness_score')]
            if any(v is None for v in x) or row.event_id=='market-fede5685cf5cbbf7111a63b1-15m':
                continue
            values.append(x);ids.append(row.event_id)
        except (TypeError,KeyError):
            continue
    id_hash=hashlib.sha256(('\n'.join(sorted(ids))+'\n').encode()).hexdigest()
    if len(values)!=60 or id_hash!='f376f9d6091e0ea229f340027bfb36fa13a665f6c7ce50d454cbc8e1cf53717c':
        raise ValueError('frozen Phi_G selector did not reproduce')
    v=np.array(values)
    mean_error=float(np.max(np.abs(v.mean(0)-PHI_G_MEAN)))
    sd_error=float(np.max(np.abs(v.std(0,ddof=1)-PHI_G_SD)))
    if max(mean_error,sd_error)>1e-12:
        raise ValueError('frozen GEX normalization did not reproduce')
    fixtures=Path(__file__).resolve().parents[1]/'research_fixtures/mathematical_edge'
    real=list(csv.DictReader((fixtures/'fx_real_bp.csv').open()))
    lookup={(r.instrument,round(r.captured_ts,3)):r for _,r in d[d.horizon_minutes==15].iterrows()}
    errors=[]
    for row in real:
        event=lookup[row['instrument'],round(float(row['ts']),3)]
        f=json.loads(event['passive__features_json'])
        errors.append(abs(float(row['gex_velocity'])-f['g1s_evidence_v3']['gex']['dynamics']['force_score']['slope']))
    if max(errors)>1e-12:
        raise ValueError('archived dot-GEX units/path did not reproduce')
    report=replay_frozen_fx_rule(fixtures)
    report.update(raw_files=expected,raw_phi_g_reconstruction={
        'selected_rows':60,'row_id_sha256':id_hash,'max_mean_error':mean_error,
        'max_sample_sd_error':sd_error,'h2_derivative_matching_rows':len(errors),
        'h2_derivative_max_error':max(errors)})
    Path(args.output).write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print('Archive reproduction PASS:',len(real),'FX rows,',report['veto_n'],'vetoes,',report['delta_gross_bp_per_base'],'gross bp/base')


if __name__=='__main__':
    main()
