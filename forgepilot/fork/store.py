import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile

from filelock import FileLock

from forgepilot.fork.models import ForkJob, ForkTaskInput, ForkTaskState
from forgepilot.fork.snapshot import ForkSnapshot
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.validation import require_complete_tools
from forgepilot.exceptions import ModelError


class ForkJobStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, job_id: str) -> Path:
        if re.fullmatch(r"[0-9a-f]{32}", job_id) is None:
            raise ValueError("Fork 作业 ID 格式不合法")
        return self.root / job_id / "job.json"

    def _save(self, path: Path, job: ForkJob | ForkSnapshot) -> None:
        temporary = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix="job-", suffix=".tmp", delete=False,
            ) as file:
                temporary = Path(file.name)
                file.write(job.model_dump_json(indent=2))
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def create(self, *, parent_session_id: str, tool_call_id: str,
               tasks: list[ForkTaskInput]) -> ForkJob:
        if len({task.name for task in tasks}) != len(tasks):
            raise ValueError("子任务名称不能重复")
        job = ForkJob(
            parent_session_id=parent_session_id, tool_call_id=tool_call_id,
            tasks=[ForkTaskState(task=task) for task in tasks],
        )
        path = self._path(job.id)
        path.parent.mkdir(parents=True, exist_ok=False)
        with FileLock(str(path) + ".lock", timeout=5):
            self._save(path, job)
        return job

    def get(self, job_id: str) -> ForkJob:
        path = self._path(job_id)
        job = ForkJob.model_validate_json(path.read_text(encoding="utf-8"))
        if job.id != job_id:
            raise ValueError("Fork 作业内容与目录 ID 不一致")
        return job

    def find_by_call(self, parent_session_id: str, tool_call_id: str) -> ForkJob | None:
        found = None
        for path in sorted(self.root.glob("*/job.json")):
            job = self.get(path.parent.name)
            if job.parent_session_id == parent_session_id and job.tool_call_id == tool_call_id:
                if found is not None:
                    raise ValueError("同一次 Fork 调用对应多个作业")
                found = job
        return found

    def save_snapshot(self, job_id: str, snapshot: ForkSnapshot) -> None:
        path = self._path(job_id)
        with FileLock(str(path) + ".lock", timeout=5):
            self.get(job_id)
            target = path.parent / "snapshot.json"
            if target.exists():
                raise ValueError("父请求快照已保存，不能覆盖")
            self._save(target, snapshot)

    def get_snapshot(self, job_id: str) -> ForkSnapshot:
        self.get(job_id)
        return ForkSnapshot.model_validate_json(
            (self._path(job_id).parent / "snapshot.json").read_text(encoding="utf-8")
        )

    def initialize_session(self, job_id: str, task_id: str, *,
                           parent_header: SessionHeader,
                           messages: list[dict]) -> SessionStore:
        """复制内部格式的父请求消息，追加子任务指令；不修改父会话。"""
        path = self._path(job_id)
        with FileLock(str(path) + ".lock", timeout=5):
            job = self.get(job_id)
            if parent_header.session_id != job.parent_session_id:
                raise ValueError("父会话不属于当前 Fork 作业")
            task = next((item for item in job.tasks if item.id == task_id), None)
            if task is None:
                raise ValueError("子任务不存在")
            if task.session_id is not None:
                raise ValueError("子会话已经初始化，请直接读取")
            if task.status != "pending":
                raise ValueError("只能初始化等待执行的子任务")
            session_path = path.parent / "tasks" / task.id / "session.jsonl"
            session_path.parent.mkdir(parents=True, exist_ok=True)
            if session_path.exists():
                existing = SessionStore(session_path)
                existing_header, existing_entries = existing.read()
                expected = [*messages, {"role": "user", "content": task.task.instruction}]
                if (existing_header.session_id != task.id
                        or existing_header.cwd != parent_header.cwd
                        or [getattr(item, "payload", None) for item in existing_entries] != expected):
                    raise ValueError("未关联的子会话与初始输入不一致，不能覆盖")
                task.session_id = task.id
                self._save(path, job)
                return existing
            temporary = session_path.with_suffix(".initializing")
            if temporary.exists():
                # 初始化与发布均在作业锁内，残留临时文件不属于活动执行者。
                temporary.unlink()
            try:
                store = SessionStore(temporary)
                store.create(SessionHeader(
                    session_id=task.id, project_id=parent_header.project_id,
                    owner_id=parent_header.owner_id, cwd=parent_header.cwd,
                ))
                for message in messages:
                    store.append_message(message)
                require_complete_tools(store.get_current_branch())
                store.append_message({
                    "role": "user", "content": task.task.instruction,
                })
                os.replace(temporary, session_path)
            finally:
                temporary.unlink(missing_ok=True)
            task.session_id = task.id
            self._save(path, job)
            return SessionStore(session_path)

    def get_session(self, job_id: str, task_id: str) -> SessionStore:
        job = self.get(job_id)
        task = next((item for item in job.tasks if item.id == task_id), None)
        if task is None or task.session_id is None:
            raise ValueError("子会话尚未初始化或子任务不存在")
        store = SessionStore(self._path(job_id).parent / "tasks" / task.id / "session.jsonl")
        header, _ = store.read()
        if header.session_id != task.session_id:
            raise ValueError("子会话 ID 与任务记录不一致")
        return store

    def transition(self, job_id: str, task_id: str, *, status: str,
                   summary: str | None = None, error: str | None = None) -> ForkJob:
        path = self._path(job_id)
        allowed = {
            "pending": {"running"},
            "running": {"completed", "failed", "interrupted"},
            "interrupted": {"running"},
            "completed": set(),
            "failed": set(),
        }
        with FileLock(str(path) + ".lock", timeout=5):
            job = self.get(job_id)
            task = next((item for item in job.tasks if item.id == task_id), None)
            if task is None:
                raise ValueError("子任务不存在")
            if status not in allowed[task.status]:
                raise ValueError(f"不能从 {task.status} 转为 {status}")
            if status == "completed" and (not summary or not summary.strip()):
                raise ValueError("完成子任务必须提供结论")
            if status in {"failed", "interrupted"} and (not error or not error.strip()):
                raise ValueError("失败或中断必须提供原因")
            updated = ForkTaskState.model_validate({
                **task.model_dump(), "status": status,
                "summary": summary if status == "completed" else None,
                "error": error if status in {"failed", "interrupted"} else None,
            })
            job.tasks = [updated if item.id == task_id else item for item in job.tasks]
            self._save(path, job)
            return job

    def reserve_request(self, job_id: str, task_id: str) -> None:
        path = self._path(job_id)
        with FileLock(str(path) + ".lock", timeout=5):
            job = self.get(job_id)
            task = next((item for item in job.tasks if item.id == task_id), None)
            if task is None or task.status != "running":
                raise ValueError("只能为执行中的子任务预留请求")
            if task.model_requests >= task.request_limit:
                raise ModelError("fork_request_limit", "子任务已达到累计模型请求上限")
            task.model_requests += 1
            self._save(path, job)
