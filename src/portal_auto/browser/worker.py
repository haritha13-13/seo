"""Playwright's sync API leaves an event loop running in its thread, and crewai refuses to run
inside one. All browser calls therefore go through one dedicated worker thread."""
from concurrent.futures import ThreadPoolExecutor
from functools import wraps


class BrowserWorker:
    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="browser")

    def call(self, fn, *args, **kwargs):
        return self._executor.submit(fn, *args, **kwargs).result()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)


def on_worker(method):
    """Run a BrowserSession method on that session's worker thread."""
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        return self.worker.call(method, self, *args, **kwargs)
    return wrapper
