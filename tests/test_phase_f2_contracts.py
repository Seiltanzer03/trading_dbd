from seiltanzer.ai_api import deterministic_result, success_body
from seiltanzer.ai_verdict import render_policy_report
from seiltanzer.decision_research import counterfactual_replay

def decision():
    return {"trade_id":1,"decision_id":"d1","policy":"CLOSE_25",
        "execution_status":"pending_execution","manual_execution_required":True,
        "incremental_close_fraction":.25,
        "remaining_fraction_before_action":1.,
        "remaining_fraction_after_action":.75,"instruction_ru":"Закрыть 25%."}

def test_llm_and_fallback_success_bodies_keep_same_decision():
    d=decision()
    llm=success_body({"verdict":"ok","model":"m","management_decision":d},"r1")
    snap={"captured_ts":1,"policy_manager":{"management_decision":d}}
    fallback=success_body(deterministic_result(snap,lambda _: "fallback"),"r2",
                          degraded=True)
    assert llm["management_decision"]==d
    assert fallback["management_decision"]==d

def test_geometry_report_has_original_stop_active_barrier_and_take():
    d=decision()
    snap={
      "trade_geometry":{"current":105,"entry":100,"original_stop":90,
        "active_risk_barrier":100,"active_risk_barrier_type":"BREAK_EVEN",
        "final_take":125,"current_r":.5,"r_to_active_stop":.5,
        "r_to_final_take":2.,"take_first":.38,"stop_or_be_first":.24,
        "no_touch":.38,"p50_resolution_minutes":184},
      "position_state":{"remaining_position_fraction":.75,
        "realized_position_fraction":.25},
      "policy_manager":{"management_decision":d,
        "recommendation":{"policy":"CLOSE_25","action_ru":"ЗАКРЫТЬ 25% ПОЗИЦИИ СЕЙЧАС",
          "remaining_fraction":.75,"remaining_management":"БУ","next_rung_r":1.},
        "policies":{name:{"expected_final_r":.1,"median_final_r":.1,
          "cvar10_r":-.5,"p_next_rung_before_stop":.4,
          "p_stop_before_next_rung":.2,"no_event_probability":{"60m":.4}}
          for name in ("HOLD","CLOSE_10","CLOSE_25","CLOSE_50","EXIT")},
        "selection_rule":{},"stability":{},"evidence":{},
        "metric_coverage":{"summary":{}},"inputs":{"r0":.5}},
      "observation":{"exact_levels":{"entry":100,"stop":90,"take":125,"current":105}},
      "strategy":{"direction":"long"}}
    report=render_policy_report(snap)
    for value in ("100","90","125","BREAK_EVEN","ГЕОМЕТРИЯ СДЕЛКИ",
                  "TAKE vs STOP/BE"):
        assert value in report
