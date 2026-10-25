"""Private projection of already-verified immutable capture bytes; stdlib only."""
from __future__ import annotations

import hashlib
import json
import re

from .real_input_integrity import FLAGS, simulated_evidence

PROJECTION_CONTRACT = 'native-family-capture-projection-v1'
ROOTS = ('macro_context_v1', 'macro_t0_context', 'event_context', 'event_reaction',
         'edge_family_sources', 'edge_family_source_bundle', 'treasury_context_v1')


def project_capture(raw, source_sha256):
    """Copy existing inputs after original/passive equality, never infer data."""
    if (not isinstance(raw,str) or len(raw.encode()) > 256000
            or hashlib.sha256(raw.encode()).hexdigest() != source_sha256):
        return None
    try:
        frozen=json.loads(raw)
        if not isinstance(frozen,dict) or simulated_evidence(frozen,recursive=False):
            return None
        kept={key:frozen[key] for key in ROOTS if key in frozen}
        metrics=frozen.get('option_distribution')
        if isinstance(metrics,dict):
            kept['option_distribution']={key:metrics[key] for key in
                (*FLAGS,'proxy','provenance','density_input','oi_profile') if key in metrics}
        for version,name in (('g1s_evidence_v3','option_static'),('g1s_evidence_v2','option_context')):
            parent=frozen.get(version)
            block=parent.get(name) if isinstance(parent,dict) else None
            if isinstance(block,dict):
                # Preserve all rejection evidence before dropping unused Greeks.
                fields=(*FLAGS,'available','proxy','iv','implied_vol_annual','quality','source_ts')
                kept[version]={name:block if simulated_evidence(block) else
                    {key:block[key] for key in fields if key in block}}
        kept['_native_capture_source_sha256']=source_sha256
        projected=json.dumps(kept,allow_nan=False,separators=(',',':'))
    except (ValueError,TypeError,RecursionError):
        return None
    if len(projected.encode()) > 256000:
        # Binding metadata must never push a valid original over the consumer
        # ceiling. The already-verified full record uses the original contract.
        return {'frozen_features_json':raw, 'features_sha256':source_sha256}
    return {'frozen_features_json':projected, 'features_sha256':source_sha256,
            'projection_contract':PROJECTION_CONTRACT,
            'projection_sha256':hashlib.sha256(projected.encode()).hexdigest()}


def verified_capture(row):
    """Verify private transport and keep the original source hash as identity.

    Original-source verification is performed by the authenticated read-only
    producer against BOTH immutable records before any projection. Its verified
    receipt remains necessary; a projection digest alone grants no authority.
    """
    raw,source=row.get('frozen_features_json'),row.get('features_sha256')
    if (row.get('source_verified') is not True or not isinstance(raw,str)
            or len(raw.encode()) > 256000 or not isinstance(source,str)
            or re.fullmatch('[0-9a-f]{64}',source) is None):
        return None
    contract=row.get('projection_contract')
    if contract is not None and contract != PROJECTION_CONTRACT:
        return None
    expected=row.get('projection_sha256') if contract else source
    if hashlib.sha256(raw.encode()).hexdigest() != expected:
        return None
    try:
        frozen=json.loads(raw)
    except (ValueError,TypeError,RecursionError):
        return None
    if not isinstance(frozen,dict) or (contract and frozen.get('_native_capture_source_sha256') != source):
        return None
    return frozen
