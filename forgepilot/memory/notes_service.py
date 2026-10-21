from filelock import Timeout

from forgepilot.exceptions import ModelError
from forgepilot.memory.notes_agent import SessionNotesAgent
from forgepilot.memory.notes_store import SessionNotesStore
from forgepilot.memory.snapshot import (
    MemorySnapshot,
    build_memory_snapshot,
)
from forgepilot.sessions.budget.token_estimator import (
    estimate_request_tokens,
)
from forgepilot.sessions.inspection import inspect_branch
from forgepilot.sessions.store import SessionStore


def estimate_history_tokens(
    snapshot: MemorySnapshot,
) -> int:
    return estimate_request_tokens({
        "messages": [
            entry.payload
            for entry in snapshot.entries
        ],
        "tools": [],
    })


class SessionNotesService:
    def __init__(
        self,
        *,
        sessions: SessionStore,
        notes_store: SessionNotesStore,
        agent: SessionNotesAgent,
        first_at: int = 10000,
        update_every: int = 5000,
    ):
        for value in (first_at, update_every):
            if type(value) is not int or value < 1:
                raise ValueError("笔记触发阈值必须是正整数")

        self.sessions = sessions
        self.notes_store = notes_store
        self.agent = agent
        self.first_at = first_at
        self.update_every = update_every

    def update_if_due(self) -> dict:
        try:
            branch = self.sessions.get_current_branch()
            state = inspect_branch(branch)

            if state in {"empty", "needs_recovery"}:
                return {"status": "not_ready"}

            snapshot = build_memory_snapshot(
                self.sessions,
                require_final=False,
            )

            history_tokens = estimate_history_tokens(
                snapshot
            )
            previous = self.notes_store.get_current()

            if previous is None:
                # 有旧文件但不适用于当前分支，需要重建。
                rebuilding = (
                    self.notes_store.state_path.exists()
                )

                if (
                    not rebuilding
                    and history_tokens < self.first_at
                ):
                    return {
                        "status": "below_threshold",
                        "history_tokens": history_tokens,
                    }

            elif (
                history_tokens - previous.history_tokens
                < self.update_every
            ):
                return {
                    "status": "below_threshold",
                    "history_tokens": history_tokens,
                }

            notes = self.agent.generate(snapshot)

            self.notes_store.save(
                notes,
                snapshot=snapshot,
                history_tokens=history_tokens,
            )

            return {
                "status": "updated",
                "history_tokens": history_tokens,
                "path": str(
                    self.notes_store.markdown_path
                ),
            }

        except Timeout:
            return {"status": "busy"}

        except (ModelError, ValueError, OSError) as exc:
            return {
                "status": "failed",
                "reason": str(exc),
            }