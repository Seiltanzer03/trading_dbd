"""Install G.1-M without changing production decision authority."""
from __future__ import annotations

from .engine import Engine
from .passive_learning import PassiveLearningEngine
from .g1_management_runtime import ManagementEdgeRuntime
from .g1_management_active_edge_t0 import install_g1_management_active_edge_t0
from .g1_management_status_nonblocking import install_g1_management_status_nonblocking


_INSTALLED = False


def install_g1_management_integration() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    # Freeze the already-present active-edge context only inside G1-M research.
    # This patch does not wrap AI Verdict or alter execution authority.
    install_g1_management_active_edge_t0()

    original_engine_init = Engine.__init__
    original_engine_close = Engine.close
    original_passive_step = PassiveLearningEngine.step

    def engine_init(self, *args, **kwargs):
        original_engine_init(self, *args, **kwargs)
        self.management = ManagementEdgeRuntime(self)
        # Status is prewarmed before server traffic/background contention and its
        # HTTP facade never touches the shared research SQLite/lock path.
        install_g1_management_status_nonblocking(self.management)
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
