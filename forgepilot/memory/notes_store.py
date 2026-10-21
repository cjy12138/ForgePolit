import os
from pathlib import Path

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field

from forgepilot.memory.notes import SessionNotes, render_session_notes
from forgepilot.memory.snapshot import MemorySnapshot
from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.store import SessionStore


class SessionNotesState(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    session_id: str = Field(min_length=1)
    source_leaf_id: str = Field(min_length=1)
    history_tokens: int = Field(ge=0)
    notes: SessionNotes


class SessionNotesStore:
    def __init__(self, sessions: SessionStore):
        self.sessions = sessions
        self.session_id = sessions.read()[0].session_id

        notes_root = (
            sessions.path.parent / "notes"
        ).resolve()

        self.root = (
            notes_root / self.session_id
        ).resolve()

        if self.root.parent != notes_root:
            raise ValueError("会话笔记目录不合法")

        self.root.mkdir(parents=True, exist_ok=True)

        self.markdown_path = self.root / "session_notes.md"
        self.state_path = self.root / "session_notes.json"

        self._lock = FileLock(
            str(self.root / ".notes.lock")
        )

    @staticmethod
    def _write_text(path: Path, text: str) -> None:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)

    def get_current(self) -> SessionNotesState | None:
        if not self.state_path.exists():
            return None

        state = SessionNotesState.model_validate_json(
            self.state_path.read_text(encoding="utf-8")
        )

        if state.session_id != self.session_id:
            raise ValueError("笔记状态不属于当前会话")

        branch_ids = {
            entry.id
            for entry in self.sessions.get_current_branch()
        }

        if state.source_leaf_id not in branch_ids:
            return None

        return state

    def save(
        self,
        notes: SessionNotes,
        *,
        snapshot: MemorySnapshot,
        history_tokens: int,
    ) -> SessionNotesState:
        notes = SessionNotes.model_validate(
            notes.model_dump()
        )

        state = SessionNotesState(
            session_id=snapshot.session_id,
            source_leaf_id=snapshot.source_leaf_id,
            history_tokens=history_tokens,
            notes=notes,
        )

        if state.session_id != self.session_id:
            raise ValueError("笔记快照不属于当前会话")

        source_ids = {
            entry.id for entry in snapshot.entries
        }
        cited_ids = notes.source_entry_ids

        if (
            len(cited_ids) != len(set(cited_ids))
            or not set(cited_ids).issubset(source_ids)
        ):
            raise ValueError("笔记引用了非法来源节点")

        markdown = render_session_notes(notes)

        with self._lock.acquire(timeout=0):
            branch = self.sessions.get_current_branch()

            if snapshot.source_leaf_id not in {
                entry.id for entry in branch
            }:
                raise ValueError("笔记来源已不在当前分支")

            current_messages = {
                entry.id: entry
                for entry in branch
                if isinstance(entry, MessageEntry)
            }

            for entry in snapshot.entries:
                current = current_messages.get(entry.id)

                if (
                    current is None
                    or current.model_dump() != entry.model_dump()
                ):
                    raise ValueError("笔记来源与快照不一致")

            self._write_text(
                self.markdown_path,
                markdown,
            )
            self._write_text(
                self.state_path,
                state.model_dump_json(),
            )

        return state