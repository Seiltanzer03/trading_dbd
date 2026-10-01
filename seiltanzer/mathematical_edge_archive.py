"""Frozen user research: reproducible diagnostics, never an order source."""
import csv
from pathlib import Path
from .mathematical_edge import h2_controller, H2_THETA, H2_MOVEMENT_THRESHOLD

RESULTS_ARCHIVE_SHA256 = '45fcb99fff145ae8d3b201f780c03d3a91255fe1e4bb61e78845ab1f7f8aa74d'
FULL_ARCHIVE_SHA256 = '5745250ed52981d72ce186973995581af8fcd929157ba3660de69f18149ccd84'
GEX_DERIVATIVE_PATH = 'g1s_evidence_v3.gex.dynamics.force_score.slope'
PHI_G_MEAN = (.06679664187294204, .0002687633333333334, -.0002097, -.0002436866666666667)
PHI_G_SD = (.6723481628411081, .0008169935748490516, .00066984614297183, .0007341548583850654)
PHI_G_LOADINGS = (-.22297579, -.63552668, -.31133441, .67041668)


def phi_g(balance, field_slope, force_slope, stiffness_slope):
    """Recovered archive coordinate; it is NOT dot-GEX or an H2 input."""
    from .mathematical_edge import number
    values = [number(v) for v in (balance, field_slope, force_slope, stiffness_slope)]
    if any(v is None for v in values):
        return None
    return sum(a * (x - m) / sd for a, x, m, sd in zip(PHI_G_LOADINGS, values, PHI_G_MEAN, PHI_G_SD))


def replay_frozen_fx_rule(fixture_dir):
    """Reproduce the supplied 167 real-bp observations and every golden row."""
    root = Path(fixture_dir)
    real = list(csv.DictReader((root / 'fx_real_bp.csv').open()))
    golden = list(csv.DictReader((root / 'fx_golden.csv').open()))
    if len(real) != len(golden) or not real:
        raise ValueError('FX fixture pairing mismatch')
    deltas, veto_n = [], 0
    for row, expected in zip(real, golden):
        if row['instrument'] != expected['instrument']:
            raise ValueError('FX instrument mismatch')
        p = float(row['m_P'])
        check = h2_controller(row['instrument'], p, float(row['gex_velocity']), 'archived-dot-gex-v1')
        veto = check['action'] == 'VETO'
        delta = -float(row['z_A']) if veto else 0.
        if (abs(check['m_price_gex'] - float(expected['m_pg'])) > 1e-12
                or veto != bool(int(expected['gex_veto_signal']))
                or abs(delta - float(expected['delta_bp'])) > 1e-10):
            raise ValueError('frozen FX row did not reproduce')
        deltas.append(delta); veto_n += int(veto)
    return {'archive_sha256': RESULTS_ARCHIVE_SHA256, 'full_archive_sha256': FULL_ARCHIVE_SHA256,
            'n': len(real), 'veto_n': veto_n, 'delta_gross_bp_per_base': sum(deltas) / len(real),
            'theta': H2_THETA, 'movement_threshold': H2_MOVEMENT_THRESHOLD,
            'gex_derivative_path': GEX_DERIVATIVE_PATH,
            'all_golden_rows_reproduced': True, 'prospective_profit_proof': False,
            'authority': 'ARCHIVE_REPRODUCTION_DIAGNOSTIC_ONLY',
            'indices_metals_gex_authority': False,
            'price_decoder_coefficients_in_results_archive': False}
