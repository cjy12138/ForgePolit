from concurrent.futures import Future, ThreadPoolExecutor
from threading import get_ident
from concurrent.futures import Future, ThreadPoolExecutor, wait
from filelock import Timeout

from forgepilot.exceptions import ModelError
from forgepilot.memory.service import (
    MemoryExtractionService,
    PreparedMemoryExtraction,
)
from threading import Event, get_ident

class BackgroundMemoryExtraction:
    def __init__(self, service: MemoryExtractionService):
        self.service = service

        self._owner = get_ident()
        self._closed = False
        self._stop = Event()

        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="memory-extraction",
        )

        self._future: Future[PreparedMemoryExtraction] | None = None

    def _check_owner(self) -> None:
        if get_ident() != self._owner:
            raise RuntimeError(
                "记忆作业的调度和发布必须在创建它的线程执行"
            )

    def start(self) -> dict:
        self._check_owner()

        if self._closed:
            return {"status": "closed"}

        if self._future is not None:
            return {"status": "pending"}

        try:
            snapshot, status = (
                self.service.prepare_completed_turn()
            )

            if snapshot is None:
                return {"status": status}

            self._stop.clear()

            self._future = self._executor.submit(
                self.service.extract_snapshot,
                snapshot,
                is_cancelled=self._stop.is_set,
            )

            return {"status": "started"}

        except Timeout:
            return {"status": "busy"}

        except (ModelError, ValueError, OSError) as exc:
            return {
                "status": "failed",
                "reason": str(exc),
            }

    def poll(self) -> dict:
        self._check_owner()

        if self._future is None:
            return {"status": "idle"}

        if not self._future.done():
            return {
                "status": (
                    "cancelling"
                    if self._stop.is_set()
                    else "running"
                )
            }

        future = self._future
        self._future = None

        if self._stop.is_set():
            return {"status": "cancelled"}

        try:
            prepared = future.result()

            return self.service.publish_prepared(prepared)


        except Timeout:

            return {"status": "busy"}


        except ModelError as exc:

            if exc.code == "memory_extraction_cancelled":
                return {"status": "cancelled"}

            return {

                "status": "failed",

                "reason": str(exc),

            }


        except (ValueError, OSError) as exc:

            return {

                "status": "failed",

                "reason": str(exc),

            }

    def finish(self) -> dict:
        self._check_owner()

        if self._future is None:
            return {"status": "idle"}

        wait([self._future])

        return self.poll()

    def close(self) -> None:
        self._check_owner()

        if self._closed:
            return

        self._closed = True
        self._stop.set()
        self._executor.shutdown(
            wait=True,
            cancel_futures=True,
        )
        self._future = None

    def cancel(self) -> dict:
        self._check_owner()

        if self._future is None:
            return {"status": "idle"}

        self._stop.set()

        return {"status": "cancel_requested"}
