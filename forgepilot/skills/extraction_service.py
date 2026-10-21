from forgepilot.sessions.inspection import inspect_branch
from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.store import SessionStore
from forgepilot.skills.extractor import SkillExtractor
from forgepilot.skills.store import SkillStore
import json
import os
from forgepilot.skills.updater import SkillUpdater
class SkillExtractionService:
    def __init__(
        self,
        *,
        sessions: SessionStore,
        skill_store: SkillStore,
        extractor: SkillExtractor,
    ):
        self.sessions = sessions
        self.skill_store = skill_store
        self.extractor = extractor
        self.updater = SkillUpdater(
            model=extractor.model,
            context_budget=extractor.context_budget,
        )
        header, _ = sessions.read()
        self.session_id = header.session_id

        self.state_path = sessions.path.with_suffix(
            ".skill-extraction.json"
        )


    def _current_task(self) -> tuple[str, list[MessageEntry]]:
        branch = self.sessions.get_current_branch()

        if inspect_branch(branch) != "completed":
            raise ValueError("当前任务尚未完成，不能提取 Skill")

        messages = [
            entry
            for entry in branch
            if isinstance(entry, MessageEntry)
        ]

        start = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if messages[index].payload.get("role") == "user"
            ),
            None,
        )

        if start is None:
            raise ValueError("当前分支没有用户任务")

        return branch[-1].id, messages[start:]

    def process_completed_task(
            self,
            *,
            allowed_tools: set[str],
    ) -> dict:
        source_leaf_id, entries = self._current_task()
        task_id = entries[0].id

        if task_id in self._load_processed():
            return {
                "status": "already_processed",
                "task_entry_id": task_id,
            }

        has_tool_call = any(
            entry.payload.get("role") == "assistant"
            and entry.payload.get("type") in {
                "tool_call",
                "tool_batch",
            }
            for entry in entries
        )

        if not has_tool_call:
            self._mark_processed(task_id)

            return {
                "status": "skipped",
                "reason": "本次任务没有实际工具调用。",
                "task_entry_id": task_id,
            }

        result = self.extractor.extract(
            entries=entries,
            allowed_tools=allowed_tools,
        )

        current_branch = self.sessions.get_current_branch()

        if (
                not current_branch
                or current_branch[-1].id != source_leaf_id
        ):
            raise ValueError("会话位置已变化，本次提取结果未发布")

        if result.candidate is None:
            self._mark_processed(task_id)

            return {
                "status": "no_skill",
                "reason": result.reason,
                "task_entry_id": task_id,
            }

        candidate = result.candidate

        existing_names = {
            metadata.name
            for metadata in self.skill_store.list_metadata()
        }

        if candidate.metadata.name in existing_names:
            existing = self.skill_store.read(
                candidate.metadata.name
            )

            update_result = self.updater.update(
                existing=existing,
                candidate=candidate,
                entries=entries,
                allowed_tools=allowed_tools,
            )

            # 更新请求可能需要一段时间，再确认来源位置。
            current_branch = self.sessions.get_current_branch()

            if (
                    not current_branch
                    or current_branch[-1].id != source_leaf_id
            ):
                raise ValueError("会话位置已变化，本次更新未发布")

            updated = update_result.updated_skill

            if updated is None or updated == existing:
                self._mark_processed(task_id)

                return {
                    "status": "unchanged",
                    "name": existing.metadata.name,
                    "reason": update_result.reason,
                    "task_entry_id": task_id,
                }

            # 锁只覆盖最终检查和保存，不覆盖模型请求。
            with self.skill_store.lock:
                current = self.skill_store.read(
                    existing.metadata.name
                )

                if current != existing:
                    raise ValueError(
                        "旧 Skill 已被修改，本次更新未发布"
                    )

                path = self.skill_store.save(
                    updated,
                    replace=True,
                )

            self._mark_processed(task_id)

            return {
                "status": "updated",
                "name": updated.metadata.name,
                "path": str(path),
                "reason": update_result.reason,
                "task_entry_id": task_id,
            }

        path = self.skill_store.save(
            candidate,
            replace=False,
        )

        self._mark_processed(task_id)

        return {
            "status": "saved",
            "name": candidate.metadata.name,
            "path": str(path),
            "reason": result.reason,
            "task_entry_id": task_id,
        }

    def _load_processed(self) -> set[str]:
        if not self.state_path.exists():
            return set()

        data = json.loads(
            self.state_path.read_text(encoding="utf-8")
        )

        if data.get("session_id") != self.session_id:
            raise ValueError("Skill 提取状态不属于当前会话")

        ids = data.get("processed_task_ids")

        if (
            not isinstance(ids, list)
            or any(not isinstance(item, str) for item in ids)
        ):
            raise ValueError("Skill 提取状态格式不合法")

        return set(ids)

    def _mark_processed(self, task_id: str) -> None:
        ids = self._load_processed()
        ids.add(task_id)

        temporary = self.state_path.with_suffix(".tmp")

        temporary.write_text(
            json.dumps(
                {
                    "session_id": self.session_id,
                    "processed_task_ids": sorted(ids),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        os.replace(temporary, self.state_path)