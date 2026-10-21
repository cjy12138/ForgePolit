from pathlib import Path
import json

from forgepilot.checkpoints.git import (
    GitCheckpointStore,
    GitRestorePlan,
    GitCheckpoint,
)
from forgepilot.checkpoints.records import CheckpointRecord
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.validation import require_complete_tools
from dataclasses import dataclass

@dataclass(frozen=True)
class SessionCheckoutPlan:
    source_leaf_id: str
    target_leaf_id: str
    checkpoint: CheckpointRecord
    git_plan: GitRestorePlan
class SessionCheckpointService:
    def __init__(
            self,
            sessions: SessionStore,
            git: GitCheckpointStore,
    ):
        header, _ = sessions.read()

        if Path(header.cwd).resolve() != git.workspace:
            raise ValueError("会话工作区与 Git 检查点仓库不一致")

        self.sessions = sessions
        self.git = git

    def create(self) -> CheckpointRecord:
        branch = self.sessions.get_current_branch()

        if not branch:
            raise ValueError("空会话不能关联代码检查点")

        # 当前调用必须已经有结果，才能建立稳定关联。
        require_complete_tools(branch)

        leaf = branch[-1]
        checkpoint = self.git.create()

        record = CheckpointRecord(
            id=checkpoint.id,
            session_id=leaf.session_id,
            target_entry_id=leaf.id,
            commit_id=checkpoint.commit_id,
            base_commit_id=checkpoint.base_commit_id,
            ref=checkpoint.ref,
        )

        self.sessions.append_checkpoint(record)
        return record

    def prepare_current_restore(self) -> GitRestorePlan:
        branch = self.sessions.get_current_branch()
        require_complete_tools(branch)

        record = self.sessions.get_current_checkpoint()

        if record is None:
            raise ValueError("当前分支没有关联的代码检查点")

        return self.git.prepare_restore(
            record.id,
            expected_commit_id=record.commit_id,
        )

    def restore_current(
        self,
        plan: GitRestorePlan,
    ) -> GitCheckpoint:
        require_complete_tools(
            self.sessions.get_current_branch()
        )

        record = self.sessions.get_current_checkpoint()

        if (
            record is None
            or record.id != plan.target.id
            or record.commit_id != plan.target.commit_id
        ):
            raise ValueError("当前分支的检查点已变化，请重新预览")

        restored = self.git.restore(plan)

        self._append_restore_notice(
            restored,
            record,
            plan,
        )
        return restored

    def _append_restore_notice(
        self,
        restored: GitCheckpoint,
        record: CheckpointRecord,
        plan: GitRestorePlan,
    ) -> None:
        self.sessions.append_message({
            "role": "user",
            "source": "checkpoint_restore",
            "content": (
                    "【ForgePilot 程序事件：代码检查点恢复】\n"
                    "程序已将工作区代码恢复到以下检查点。"
                    "原始任务要求和访问限制仍然有效。"
                    "历史中的文件内容和测试结果可能已经过期，"
                    "继续执行前应重新读取相关文件，并按需要重新验证。"
                    "恢复不代表任务已经完成，也不授予额外权限。\n"
                    + json.dumps(
                {
                    "checkpoint_id": restored.id,
                    "commit_id": restored.commit_id,
                    "checkpoint_entry_id": record.target_entry_id,
                    "backup_checkpoint_id": plan.backup.id,
                },
                ensure_ascii=False,
            )
            ),
        })


    def save_if_changed(self) -> CheckpointRecord | None:
        branch = self.sessions.get_current_branch()

        if not branch:
            raise ValueError("空会话不能关联代码检查点")

        # 不能在一批工具结果尚未收齐时保存关联。
        require_complete_tools(branch)

        previous = self.sessions.get_current_checkpoint()
        previous_commit_id = None

        if previous is not None:
            checkpoint = self.git.get(previous.id)

            if checkpoint.commit_id != previous.commit_id:
                raise ValueError(
                    "Git 引用与会话保存的检查点提交不一致"
                )

            previous_commit_id = checkpoint.commit_id

        checkpoint = self.git.create_if_changed(
            previous_commit_id
        )

        if checkpoint is None:
            return None

        leaf = branch[-1]

        record = CheckpointRecord(
            id=checkpoint.id,
            session_id=leaf.session_id,
            target_entry_id=leaf.id,
            commit_id=checkpoint.commit_id,
            base_commit_id=checkpoint.base_commit_id,
            ref=checkpoint.ref,
        )

        self.sessions.append_checkpoint(record)
        return record

    def prepare_checkout(
        self,
        target_leaf_id: str,
    ) -> SessionCheckoutPlan:
        current_branch = self.sessions.get_current_branch()

        if not current_branch:
            raise ValueError("空会话不能执行切换恢复")

        require_complete_tools(current_branch)

        target_branch = self.sessions.get_branch(
            target_leaf_id
        )
        require_complete_tools(target_branch)

        record = self.sessions.get_checkpoint_at(
            target_leaf_id
        )

        if record is None:
            raise ValueError("目标位置没有可恢复的代码检查点")

        git_plan = self.git.prepare_restore(
            record.id,
            expected_commit_id=record.commit_id,
        )

        return SessionCheckoutPlan(
            source_leaf_id=current_branch[-1].id,
            target_leaf_id=target_leaf_id,
            checkpoint=record,
            git_plan=git_plan,
        )

    def checkout(
        self,
        plan: SessionCheckoutPlan,
    ) -> GitCheckpoint:
        current_branch = self.sessions.get_current_branch()

        if (
            not current_branch
            or current_branch[-1].id != plan.source_leaf_id
        ):
            raise ValueError("当前会话位置已变化，请重新预览")

        require_complete_tools(current_branch)

        target_branch = self.sessions.get_branch(
            plan.target_leaf_id
        )
        require_complete_tools(target_branch)

        record = self.sessions.get_checkpoint_at(
            plan.target_leaf_id
        )

        if (
            record is None
            or record != plan.checkpoint
            or record.id != plan.git_plan.target.id
            or record.commit_id != plan.git_plan.target.commit_id
        ):
            raise ValueError("目标检查点已变化，请重新预览")

        # Git 层会检查预览后的工作区是否发生变化。
        restored = self.git.restore(plan.git_plan)

        # 代码恢复成功后，再切换对话位置。
        self.sessions.move_cursor(plan.target_leaf_id)

        # 通知接在目标节点后面，形成新的续接分支。
        self._append_restore_notice(
            restored,
            record,
            plan.git_plan,
        )

        return restored

