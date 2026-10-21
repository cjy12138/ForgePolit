import os
from pathlib import Path

from forgepilot.sessions.models import (
    CursorChanged,
    MessageEntry,
    SessionHeader,
    record_adapter,
)
from typing import Any
from forgepilot.sessions.validation import (
    pending_tool_calls,
    require_complete_tools,
)
import json
from forgepilot.sessions.models import CompactionEntry, SessionNode
from forgepilot.sessions.compaction.planning import validate_compaction_entry
from forgepilot.sessions.snip.records import SnipRecord
from forgepilot.sessions.snip.validation import validate_snip_record
from forgepilot.sessions.collapse.records import CollapseRecord
from forgepilot.sessions.collapse.validation import (
    validate_collapse_record,
)
from forgepilot.checkpoints.records import CheckpointRecord

class SessionStore:
    def __init__(self,path: Path):
        self.path = path

    def create(self,header:SessionHeader):
        self.path.parent.mkdir(parents=True,exist_ok=True)

        with self.path.open(
            "x",encoding="utf-8",newline="\n"
        ) as file:
            file.write(header.model_dump_json()+"\n")
            file.flush()
            os.fsync(file.fileno())

    def _replay(
            self,
    ) -> tuple[
        SessionHeader,
        list[SessionNode],
        str | None,
        list[SnipRecord],
        list[CollapseRecord],
        list[CheckpointRecord],
    ]:
        with self.path.open(
            "r",encoding="utf-8",newline="\n"
        ) as file:
            lines = file.readlines()

        if not lines:
            raise ValueError("会话文件为空")

        for number, line in enumerate(lines, start=1):
            if not line.endswith("\n"):
                raise ValueError(
                    f"会话文件第 {number} 行没有完整结束"
                )

        header = SessionHeader.model_validate_json(lines[0])
        entries = []
        known_ids = set()
        active_leaf_id = None

        snip_records = []
        snip_ids = set()

        collapse_records = []
        collapse_ids = set()

        checkpoint_records = []
        checkpoint_ids = set()

        for line in lines[1:]:
            record = record_adapter.validate_json(line)

            if isinstance(record, (MessageEntry, CompactionEntry)):
                self._validate_entry(record, header, known_ids)
                if isinstance(record, CompactionEntry):
                    if record.parent_id != active_leaf_id:
                        raise ValueError("压缩节点未接在当时的当前节点之后")

                    source_branch = self._branch_to(
                        entries,
                        record.source_leaf_id,
                    )
                    validate_compaction_entry(record, source_branch)

                entries.append(record)
                known_ids.add(record.id)

                # 新消息写入后，它成为当前选中节点。
                active_leaf_id = record.id
            elif isinstance(record, SnipRecord):
                if record.session_id != header.session_id:
                    raise ValueError("Snip 记录不属于当前会话")

                if record.id in snip_ids:
                    raise ValueError("Snip 记录 ID 重复")

                if record.source_leaf_id != active_leaf_id:
                    raise ValueError("Snip 锚点不是写入时的当前节点")

                source_branch = self._branch_to(
                    entries,
                    record.source_leaf_id,
                )
                validate_snip_record(record, source_branch)

                snip_records.append(record)
                snip_ids.add(record.id)
            elif isinstance(record, CollapseRecord):
                if record.session_id != header.session_id:
                    raise ValueError("Collapse 记录不属于当前会话")

                if record.id in collapse_ids:
                    raise ValueError("Collapse 记录 ID 重复")

                if record.source_leaf_id != active_leaf_id:
                    raise ValueError(
                        "Collapse 锚点不是写入时的当前节点"
                    )

                source_branch = self._branch_to(
                    entries,
                    record.source_leaf_id,
                )

                validate_collapse_record(
                    record,
                    source_branch,
                )

                collapse_records.append(record)
                collapse_ids.add(record.id)
            elif isinstance(record, CheckpointRecord):
                if record.session_id != header.session_id:
                    raise ValueError("检查点记录不属于当前会话")

                if record.id in checkpoint_ids:
                    raise ValueError("检查点记录 ID 重复")

                if record.target_entry_id != active_leaf_id:
                    raise ValueError("检查点未关联写入时的当前节点")

                branch = self._branch_to(
                    entries,
                    record.target_entry_id,
                )
                require_complete_tools(branch)

                checkpoint_records.append(record)
                checkpoint_ids.add(record.id)
            else:
                if record.session_id != header.session_id:
                    raise ValueError("游标事件不属于当前会话")

                if record.target_id not in known_ids:
                    raise ValueError("游标指向的节点不存在")

                active_leaf_id = record.target_id
        return (
            header,
            entries,
            active_leaf_id,
            snip_records,
            collapse_records,
            checkpoint_records,
        )

    def replay(
        self,
    ) -> tuple[SessionHeader, list[SessionNode], str | None]:
        header, entries, active_leaf_id, _ , _, _= self._replay()
        return header, entries, active_leaf_id

    def read(self) -> tuple[SessionHeader, list[SessionNode]]:
        header, entries, _ = self.replay()
        return header, entries

    def move_cursor(self, target_id: str) -> None:
        header, entries = self.read()
        known_ids = {entry.id for entry in entries}

        if target_id not in known_ids:
            raise ValueError("游标指向的节点不存在")

        # 当前工具交互未完成时，不允许普通分支切换。
        require_complete_tools(self.get_current_branch())

        # 目标也必须位于工具结果已经收齐的位置。
        require_complete_tools(self.get_branch(target_id))

        event = CursorChanged(
            session_id=header.session_id,
            target_id=target_id,
        )

        with self.path.open(
                "a", encoding="utf-8", newline="\n"
        ) as file:
            file.write(event.model_dump_json() + "\n")
            file.flush()
            os.fsync(file.fileno())

    def get_current_branch(self) -> list[SessionNode]:
        _, _, active_leaf_id = self.replay()

        if active_leaf_id is None:
            return []

        return self.get_branch(active_leaf_id)



    @staticmethod
    def _validate_entry(
            entry: SessionNode,
            header: SessionHeader,
            known_ids: set[str]
    ) -> None:
        if entry.session_id != header.session_id:
            raise ValueError("消息不属于当前会话")

        if entry.id in known_ids:
            raise ValueError("消息节点 ID 重复")

        if entry.parent_id is None:
            if known_ids:
                raise ValueError("当前会话已经存在根消息")
        elif entry.parent_id not in known_ids:
            raise ValueError("父节点不存在或尚未写入")

    def append(self, entry: SessionNode) -> None:
        header, entries, active_leaf_id = self.replay()
        known_ids = {item.id for item in entries}

        self._validate_entry(entry, header, known_ids)

        if isinstance(entry, CompactionEntry):
            if entry.parent_id != active_leaf_id:
                raise ValueError("当前会话位置已变化，不能发布压缩节点")

            source_branch = self._branch_to(
                entries,
                entry.source_leaf_id,
            )
            validate_compaction_entry(entry, source_branch)

        with self.path.open(
                "a", encoding="utf-8", newline="\n"
        ) as file:
            file.write(entry.model_dump_json() + "\n")
            file.flush()
            os.fsync(file.fileno())

    @staticmethod
    def _branch_to(
            entries: list[SessionNode],
            leaf_id: str,
    ) -> list[SessionNode]:
        by_id = {entry.id: entry for entry in entries}

        if leaf_id not in by_id:
            raise ValueError("目标节点不存在")

        branch = []
        current_id = leaf_id

        while current_id is not None:
            entry = by_id[current_id]
            branch.append(entry)
            current_id = entry.parent_id

        branch.reverse()
        return branch

    def get_branch(self, leaf_id: str) -> list[SessionNode]:
        _, entries = self.read()
        return self._branch_to(entries, leaf_id)

    def append_message(
            self,
            payload: dict[str, Any],
    ) -> MessageEntry:
        header, _, active_leaf_id = self.replay()

        # 找到新消息将要接上的那条分支。
        branch = (
            self.get_branch(active_leaf_id)
            if active_leaf_id is not None
            else []
        )

        entry = MessageEntry(
            session_id=header.session_id,
            parent_id=active_leaf_id,
            payload=payload,
        )

        # 先检查“已有分支 + 新消息”，通过后才能写入。
        pending_tool_calls([*branch, entry])

        self.append(entry)
        return entry

    def mark_pending_tools_unknown(
            self,
            *,
            expected_leaf_id: str,
    ) -> list[str]:
        branch = self.get_current_branch()

        if not branch or branch[-1].id != expected_leaf_id:
            raise ValueError("当前会话位置已变化，请重新检查中断状态")

        pending = pending_tool_calls(branch)
        marked = []

        for call_id in sorted(pending):
            self.append_message({
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps(
                    {
                        "source": "session_recovery",
                        "status": "execution_unknown",
                        "message": (
                            "该调用缺少已保存的工具结果。"
                            "无法确定是否执行、是否完成或是否产生副作用。"
                            "这是恢复流程生成的说明，不是工具的实际输出。"
                            "继续任务前应先检查当前文件或执行状态，"
                            "不要直接假定成功，也不要盲目重复执行。"
                        ),
                    },
                    ensure_ascii=False,
                ),
                "is_error": True,
                "exit_code": None,
            })

            marked.append(call_id)

        return marked

    def append_snip(self, record: SnipRecord) -> bool:
        header, entries, active_leaf_id, records, _ , _= self._replay()

        if record.session_id != header.session_id:
            raise ValueError("Snip 记录不属于当前会话")

        if record.source_leaf_id != active_leaf_id:
            raise ValueError("当前会话位置已变化，不能发布 Snip 记录")

        branch = self._branch_to(entries, record.source_leaf_id)
        validate_snip_record(record, branch)

        # 相同位置、相同来源和相同策略，无需重复写入。
        key = (
            record.source_leaf_id,
            record.target_entry_id,
            record.source_sha256,
            record.policy_version,
            record.head_chars,
            record.tail_chars,
        )

        for existing in records:
            existing_key = (
                existing.source_leaf_id,
                existing.target_entry_id,
                existing.source_sha256,
                existing.policy_version,
                existing.head_chars,
                existing.tail_chars,
            )

            if existing_key == key:
                return False

        if any(existing.id == record.id for existing in records):
            raise ValueError("Snip 记录 ID 重复")

        with self.path.open(
            "a",
            encoding="utf-8",
            newline="\n",
        ) as file:
            file.write(record.model_dump_json() + "\n")
            file.flush()
            os.fsync(file.fileno())

        return True

    def get_current_snip_records(self) -> list[SnipRecord]:
        _, entries, active_leaf_id, records, _, _ = self._replay()

        if active_leaf_id is None:
            return []

        branch = self._branch_to(entries, active_leaf_id)
        branch_ids = {node.id for node in branch}

        applicable = []

        for record in records:
            if record.source_leaf_id not in branch_ids:
                continue

            validate_snip_record(record, branch)
            applicable.append(record)

        return applicable

    def append_collapse(
        self,
        record: CollapseRecord,
    ) -> bool:
        header, entries, active_leaf_id, _, records, _ = (
            self._replay()
        )

        if record.session_id != header.session_id:
            raise ValueError("Collapse 记录不属于当前会话")

        if record.source_leaf_id != active_leaf_id:
            raise ValueError(
                "当前会话位置已变化，不能发布 Collapse 记录"
            )

        branch = self._branch_to(
            entries,
            record.source_leaf_id,
        )

        validate_collapse_record(record, branch)

        # 同一锚点、同一范围、同一来源与策略，不重复保存。
        key = (
            record.source_leaf_id,
            record.source_start_id,
            record.source_end_id,
            record.source_sha256,
            record.policy_version,
            record.summary_version,
        )

        for existing in records:
            existing_key = (
                existing.source_leaf_id,
                existing.source_start_id,
                existing.source_end_id,
                existing.source_sha256,
                existing.policy_version,
                existing.summary_version,
            )

            if existing_key == key:
                return False

        if any(existing.id == record.id for existing in records):
            raise ValueError("Collapse 记录 ID 重复")

        with self.path.open(
            "a",
            encoding="utf-8",
            newline="\n",
        ) as file:
            file.write(record.model_dump_json() + "\n")
            file.flush()
            os.fsync(file.fileno())

        return True

    def get_current_collapse_records(
        self,
    ) -> list[CollapseRecord]:
        _, entries, active_leaf_id, _, records, _ = self._replay()

        if active_leaf_id is None:
            return []

        branch = self._branch_to(
            entries,
            active_leaf_id,
        )
        branch_ids = {node.id for node in branch}

        applicable = []

        for record in records:
            if record.source_leaf_id not in branch_ids:
                continue

            validate_collapse_record(record, branch)
            applicable.append(record)

        return applicable

    def append_checkpoint(
        self,
        record: CheckpointRecord,
    ) -> None:
        header, entries, active_leaf_id, _, _, records = (
            self._replay()
        )

        if record.session_id != header.session_id:
            raise ValueError("检查点记录不属于当前会话")

        if record.target_entry_id != active_leaf_id:
            raise ValueError("当前会话位置已变化，不能关联检查点")

        if any(item.id == record.id for item in records):
            raise ValueError("检查点记录 ID 重复")

        branch = self._branch_to(
            entries,
            record.target_entry_id,
        )
        require_complete_tools(branch)

        with self.path.open(
            "a", encoding="utf-8", newline="\n",
        ) as file:
            file.write(record.model_dump_json() + "\n")
            file.flush()
            os.fsync(file.fileno())

    def get_checkpoint_at(
            self,
            leaf_id: str,
    ) -> CheckpointRecord | None:
        _, entries, _, _, _, records = self._replay()

        branch = self._branch_to(entries, leaf_id)

        positions = {
            node.id: index
            for index, node in enumerate(branch)
        }

        selected = None
        selected_depth = -1

        for record in records:
            depth = positions.get(record.target_entry_id)

            # 检查点不在目标节点的祖先链上。
            if depth is None:
                continue

            # 优先选择距离目标节点最近的检查点；
            # 同一节点有多个检查点时，选择最后写入的。
            if depth >= selected_depth:
                selected = record
                selected_depth = depth

        return selected

    def get_current_checkpoint(
            self,
    ) -> CheckpointRecord | None:
        branch = self.get_current_branch()

        if not branch:
            return None

        return self.get_checkpoint_at(branch[-1].id)
