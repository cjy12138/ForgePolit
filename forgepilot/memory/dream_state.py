
import os
import time

from pydantic import BaseModel, ConfigDict, Field

from forgepilot.memory.store import MarkdownMemoryStore


class DreamState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 第一次启用时建立时间基线。
    baseline_at: float

    # 只有整理成功，才更新这个时间。
    last_success_at: float | None = None

    # 各会话最近一次完成的回答节点。
    activity: dict[str, str] = Field(default_factory=dict)

    # 上次成功整理时已经处理过的活动。
    processed: dict[str, str] = Field(default_factory=dict)


class AutoDreamStateStore:
    def __init__(self, memory_store: MarkdownMemoryStore):
        self.memory_store = memory_store
        self.path = memory_store.root / ".autodream.json"

    def _load(self, now: float) -> DreamState:
        if not self.path.exists():
            return DreamState(baseline_at=now)

        return DreamState.model_validate_json(
            self.path.read_text(encoding="utf-8")
        )

    def _save(self, state: DreamState) -> None:
        temporary = self.path.with_suffix(".tmp")

        temporary.write_text(
            state.model_dump_json(indent=2),
            encoding="utf-8",
        )

        os.replace(temporary, self.path)

    def record_activity(
        self,
        session_id: str,
        final_id: str,
        *,
        now: float | None = None,
    ) -> None:
        if not session_id or not final_id:
            raise ValueError("会话 ID 和回答节点 ID 不能为空")

        current_time = time.time() if now is None else now

        with self.memory_store.write_lock():
            state = self._load(current_time)
            state.activity[session_id] = final_id
            self._save(state)

    def prepare_if_due(
        self,
        *,
        now: float | None = None,
    ) -> dict:
        current_time = time.time() if now is None else now

        with self.memory_store.write_lock():
            state = self._load(current_time)

            # 首次检查也要保存基线，后续才能计算时间。
            if not self.path.exists():
                self._save(state)

            new_activity = {
                session_id: final_id
                for session_id, final_id in state.activity.items()
                if state.processed.get(session_id) != final_id
            }

            reference_time = (
                state.last_success_at
                if state.last_success_at is not None
                else state.baseline_at
            )

            elapsed = current_time - reference_time

            if elapsed < 24 * 60 * 60:
                return {"status": "too_soon"}

            if len(new_activity) < 5:
                return {
                    "status": "not_enough_sessions",
                    "sessions": len(new_activity),
                }

            return {
                "status": "ready",
                "activity": dict(state.activity),
                "new_sessions": len(new_activity),
            }

    def mark_success(
            self,
            activity: dict[str, str],
            *,
            now: float | None = None,
    ) -> None:
        current_time = time.time() if now is None else now

        with self.memory_store.write_lock():
            state = self._load(current_time)

            state.last_success_at = current_time

            # 只标记本次整理开始时捕获的活动。
            state.processed = dict(activity)

            # 保留当前 activity，其中可能已有整理期间的新活动。
            self._save(state)
