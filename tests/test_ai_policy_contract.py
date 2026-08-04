from seiltanzer.ai_policy import POLICY_FRACTIONS


def test_ai_policy_action_contract_is_closed_and_explicit():
    """The language model may only explain these deterministic actions."""
    assert POLICY_FRACTIONS == {
        "HOLD": 0.0,
        "CLOSE_10": 0.10,
        "CLOSE_25": 0.25,
        "CLOSE_50": 0.50,
        "EXIT": 1.0,
    }
