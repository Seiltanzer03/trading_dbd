"""One admitted AI stage, isolated from slow market-refresh worker queues."""
from concurrent.futures import ThreadPoolExecutor
import threading


class AIReviewWorkerBusy(RuntimeError):
    pass


class ReviewExecutor:
    def __init__(self):
        self._admission = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ai-review")

    @property
    def busy(self):
        return self._admission.locked()

    def submit(self, function, *args, **kwargs):
        if not self._admission.acquire(blocking=False):
            raise AIReviewWorkerBusy("AI_REVIEW_WORKER_BUSY")
        try:
            future = self._pool.submit(function, *args, **kwargs)
        except BaseException:
            self._admission.release()
            raise
        # Cancelling the HTTP await does not release a running synchronous job.
        # Completion/cancellation of the actual concurrent future owns release.
        future.add_done_callback(lambda _: self._admission.release())
        return future


_EXECUTOR = ReviewExecutor()


def review_executor():
    return _EXECUTOR
