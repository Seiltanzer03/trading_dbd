from seiltanzer.app import create_app
from seiltanzer.app_extensions import install_ai_decision_routes
from seiltanzer.config import Settings


def _ack_post_routes(app):
    return [
        route for route in app.routes
        if getattr(route, "path", None) == "/api/ai/decision/ack"
        and "POST" in (getattr(route, "methods", set()) or set())
    ]


def test_create_app_has_exactly_one_canonical_ack_route(tmp_path):
    app = create_app(Settings(data_dir=str(tmp_path), demo=True))
    before = _ack_post_routes(app)
    assert len(before) == 1
    install_ai_decision_routes(app)
    after = _ack_post_routes(app)
    assert len(after) == 1
    assert app.state.ai_decision_route_source == "canonical_position_state"
    app.state.engine.close()
