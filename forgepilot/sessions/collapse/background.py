from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, get_ident

from forgepilot.sessions.collapse.collapser import PreparedCollapse, SessionCollapser


class BackgroundCollapse:
    """Single background generator; session access and publication stay on the owner thread."""

    def __init__(self, collapser: SessionCollapser):
        self.collapser = collapser
        self.store = collapser.store
        self._owner = get_ident()
        self._stop = Event()
        previous_cancel = collapser.is_cancelled
        collapser.is_cancelled = lambda: self._stop.is_set() or (
            previous_cancel is not None and previous_cancel()
        )
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="collapse")
        self._future: Future[PreparedCollapse | None] | None = None
        self._last_leaf_id: str | None = None
        self._enable_snip = True
        self._closed = False

    def _check_owner(self):
        if get_ident() != self._owner:
            raise RuntimeError("后台 Collapse 的调度和发布必须在创建它的线程执行")

    def start(self, *, enable_snip: bool = True) -> bool:
        self._check_owner()
        if self._closed or self._future is not None:
            return False
        branch = self.store.get_current_branch()
        if not branch or branch[-1].id == self._last_leaf_id:
            return False
        records = self.store.get_current_collapse_records()
        snips = self.store.get_current_snip_records() if enable_snip else []
        self._future = self._executor.submit(
            self.collapser.prepare_candidate,
            branch=branch,
            records=records,
            snip_records=snips,
            enable_snip=enable_snip,
        )
        self._last_leaf_id = branch[-1].id
        self._enable_snip = enable_snip
        return True

    def commit_ready(self):
        self._check_owner()
        if self._future is None or not self._future.done():
            return "pending", None
        future = self._future
        self._future = None
        prepared = future.result()
        if prepared is None:
            return "skipped", None
        record = self.collapser.publish_candidate(
            prepared, enable_snip=self._enable_snip,
        )
        return ("published", record) if record is not None else ("skipped", None)

    def close(self):
        self._check_owner()
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        # An in-flight API call finishes under its client timeout; never publish on shutdown.
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._future = None
