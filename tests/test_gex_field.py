import math

import numpy as np
import pytest

from seiltanzer.core.gex_field import analytic_gex_field


def test_analytic_gex_gradient_and_curvature_match_finite_difference():
    strikes = np.array([90, 95, 100, 105, 110], dtype=float)
    gex = np.array([-4, -1, 6, 3, -2], dtype=float)
    spot = 101.25
    bandwidth = 4.75
    analytic = analytic_gex_field(strikes, gex, spot, bandwidth=bandwidth)
    step = 1e-3
    low = analytic_gex_field(strikes, gex, spot - step, bandwidth=bandwidth)
    high = analytic_gex_field(strikes, gex, spot + step, bandwidth=bandwidth)
    gradient_fd = (high["field"] - low["field"]) / (2 * step)
    curvature_fd = (high["field"] - 2 * analytic["field"] + low["field"]) / step**2
    assert analytic["gradient"] == pytest.approx(gradient_fd, rel=1e-6, abs=1e-8)
    assert analytic["stiffness"] == pytest.approx(curvature_fd, rel=2e-5, abs=1e-7)
    assert analytic["force"] == pytest.approx(-gradient_fd, rel=1e-6)
    assert analytic["family"] == "option_distribution"
    assert analytic["independent_vote"] is False
    assert all(-1 <= analytic[key] <= 1
               for key in ("field_score", "force_score", "stiffness_score"))

