"""Install G.1-M and bounded active-edge AI context without changing execution authority."""
from __future__ import annotations

from .engine import Engine
from .passive_learning import PassiveLearningEngine
from .g1_management_runtime import ManagementEdgeRuntime
from .active_edge_ai_integration import install_active_edge_ai_integration


_INSTALLED = False


def install_g1_management_integration() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_engine_init = Engine.__init__
    original_engine_close = Engine.close
    original_passive_step = PassiveLearningEngine.step

    def engine_init(self, *args, **kwargs):
        original_engine_init(self, *args, **kwargs)
        self.management = ManagementEdgeRuntime(self)
        # The passive loop is already the durable research scheduler. Reuse it
        # instead of starting another clock/thread with independent cadence.
        self.passive._g1m_runtime = self.management

    def engine_close(self, *args, **kwargs):
        runtime = getattr(self, "management", None)
        if runtime is not None:
            runtime.close()
        return original_engine_close(self, *args, **kwargs)

    def passive_step(self, *args, **kwargs):
        result = original_passive_step(self, *args, **kwargs)
        runtime = getattr(self, "_g1m_runtime", None)
        if runtime is not None:
            runtime.step()
        return result

    Engine.__init__ = engine_init
    Engine.close = engine_close
    PassiveLearningEngine.step = passive_step

    # AI Verdict consumes only compact off-host active-edge reports. This does
    # not mutate the deterministic position/execution authority.
    install_active_edge_ai_integration()
