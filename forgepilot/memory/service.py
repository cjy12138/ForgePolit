import json
import os

from filelock import Timeout

from forgepilot.exceptions import ModelError
from forgepilot.memory.extraction import MemoryExtractionAgent
from forgepilot.memory.publication import publish_memories
from forgepilot.memory.snapshot import build_memory_snapshot
from forgepilot.memory.store import MarkdownMemoryStore
from forgepilot.sessions.inspection import inspect_branch
from forgepilot.sessions.store import SessionStore
from dataclasses import dataclass

from forgepilot.memory.models import MemoryProposal
from forgepilot.memory.snapshot import MemorySnapshot
from forgepilot.memory.dream_state import AutoDreamStateStore


@dataclass(frozen=True)
class PreparedMemoryExtraction:
    snapshot: MemorySnapshot
    proposal: MemoryProposal
    read_versions: dict[str, str]

class MemoryExtractionService:
    def __init__(
        self,
        *,
        sessions: SessionStore,
        memory_store: MarkdownMemoryStore,
        agent: MemoryExtractionAgent,
    ):
        self.sessions = sessions
        self.memory_store = memory_store
        self.agent = agent

        self.session_id = sessions.read()[0].session_id
        self.state_path = sessions.path.with_suffix(
            ".memory-extraction.json"
        )
        self.dream_state = AutoDreamStateStore(memory_store)

    def prepare_completed_turn(
            self,
    ) -> tuple[MemorySnapshot | None, str]:
        branch = self.sessions.get_current_branch()

        if inspect_branch(branch) != "completed":
            return None, "not_ready"

        snapshot = build_memory_snapshot(self.sessions)
        final_id = snapshot.entries[-1].id

        with self.memory_store.write_lock():
            processed = self._load_processed()

            if final_id in processed:
                return None, "already_processed"

        return snapshot, "prepared"

    def extract_snapshot(
            self,
            snapshot: MemorySnapshot,
            *,
            is_cancelled=None,
    ) -> PreparedMemoryExtraction:
        proposal = self.agent.extract(
            snapshot,
            is_cancelled=is_cancelled,
        )

        return PreparedMemoryExtraction(
            snapshot=snapshot,
            proposal=proposal.model_copy(deep=True),
            read_versions=dict(self.agent.read_versions),
        )

    def publish_prepared(
            self,
            prepared: PreparedMemoryExtraction,
    ) -> dict:
        snapshot = prepared.snapshot
        final_id = snapshot.entries[-1].id

        with self.memory_store.write_lock():
            processed = self._load_processed()

            if final_id in processed:
                return {"status": "already_processed"}

            old_index = self.memory_store.get_index_context()["indexed_filenames"]

            documents = publish_memories(
                prepared.proposal,
                snapshot=snapshot,
                sessions=self.sessions,
                memory_store=self.memory_store,
                read_versions=prepared.read_versions,
            )

            # 记忆发布成功后，登记本轮会话活动。
            self.dream_state.record_activity(
                session_id=self.session_id,
                final_id=final_id,
            )

            # 最后标记本轮提取已经处理。
            processed.add(final_id)
            self._save_processed(processed)
            selected = self.memory_store.get_index_context()["indexed_filenames"]

        return {
            "status": (
                "published" if documents or selected != old_index else "no_memories"
            ),
            "filenames": [
                document.filename
                for document in documents
            ],
            "indexed_filenames": selected,
            "unindexed_filenames": [
                document.filename for document in documents
                if document.filename not in selected
            ],
        }

    def _load_processed(self) -> set[str]:
        if not self.state_path.exists():
            return set()

        data = json.loads(
            self.state_path.read_text(encoding="utf-8")
        )

        if data.get("session_id") != self.session_id:
            raise ValueError("提取状态不属于当前会话")

        ids = data.get("processed_final_ids")

        if (
            not isinstance(ids, list)
            or any(not isinstance(item, str) for item in ids)
        ):
            raise ValueError("提取状态格式不合法")

        return set(ids)

    def _save_processed(self, ids: set[str]) -> None:
        data = {
            "session_id": self.session_id,
            "processed_final_ids": sorted(ids),
        }

        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temporary, self.state_path)

    def process_completed_turn(self) -> dict:
        try:
            snapshot, status = self.prepare_completed_turn()

            if snapshot is None:
                return {"status": status}

            prepared = self.extract_snapshot(snapshot)

            return self.publish_prepared(prepared)

        except Timeout:
            return {"status": "busy"}

        except (ModelError, ValueError, OSError) as exc:
            return {
                "status": "failed",
                "reason": str(exc),
            }
