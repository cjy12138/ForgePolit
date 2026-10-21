from pathlib import Path

from filelock import FileLock

from forgepilot.contracts import ToolContext
from forgepilot.fork.models import ForkJob, ForkTasksInput
from forgepilot.fork.snapshot import ForkSnapshot
from forgepilot.fork.store import ForkJobStore
from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.validation import pending_tool_calls


class ForkService:
    def __init__(self, *, jobs: ForkJobStore, parent_session: SessionStore):
        self.jobs = jobs
        self.parent_session = parent_session

    def prepare(self, params: ForkTasksInput, context: ToolContext, *,
                tool_call_id: str, snapshot: ForkSnapshot | None = None) -> ForkJob:
        """准备持久化输入，不执行子任务，也不回填父工具结果。"""
        header, _ = self.parent_session.read()
        if Path(header.cwd).resolve() != context.workspace.resolve():
            raise ValueError("Fork 工作区与父会话不一致")
        branch = self.parent_session.get_current_branch()
        if tool_call_id not in pending_tool_calls(branch):
            raise ValueError("Fork 调用不是当前分支的待完成调用")
        call = None
        for entry in branch:
            if not isinstance(entry, MessageEntry) or entry.payload.get("role") != "assistant":
                continue
            message = entry.payload
            calls = message.get("calls", []) if message.get("type") == "tool_batch" else [message]
            for item in calls:
                if item.get("id") == tool_call_id:
                    call = item
        if call is None or call.get("name") != "fork_tasks":
            raise ValueError("待完成调用不是 fork_tasks")
        if ForkTasksInput.model_validate(call["arguments"]) != params:
            raise ValueError("派发参数与父会话记录不一致")

        self.jobs.root.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.jobs.root / "prepare.lock"), timeout=5):
            job = self.jobs.find_by_call(header.session_id, tool_call_id)
            if job is None:
                if snapshot is None:
                    raise ValueError("首次派发必须提供父请求快照")
                job = self.jobs.create(
                    parent_session_id=header.session_id, tool_call_id=tool_call_id,
                    tasks=params.tasks,
                )
            if [task.task for task in job.tasks] != params.tasks:
                raise ValueError("已有作业的任务参数不一致")
            try:
                saved = self.jobs.get_snapshot(job.id)
            except FileNotFoundError:
                if snapshot is None:
                    raise ValueError("作业缺少父请求快照") from None
                self.jobs.save_snapshot(job.id, snapshot)
                saved = snapshot
            if snapshot is not None and saved.sha256 != snapshot.sha256:
                raise ValueError("父请求快照与已有作业不一致")
            task = job.tasks[0]
            if task.session_id is None:
                self.jobs.initialize_session(
                    job.id, task.id, parent_header=header, messages=saved.messages(),
                )
            else:
                self.jobs.get_session(job.id, task.id)
            return self.jobs.get(job.id)
