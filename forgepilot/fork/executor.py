from collections.abc import Callable

from filelock import FileLock

from forgepilot.agent import Agent
from forgepilot.fork.models import ForkTaskState
from forgepilot.fork.snapshot import ForkSnapshot, encode
from forgepilot.fork.store import ForkJobStore
from forgepilot.contracts import ToolResult
from forgepilot.sessions.store import SessionStore


class ForkExecutor:
    def __init__(self, *, jobs: ForkJobStore,
                 create_agent: Callable[[SessionStore, ForkSnapshot, ForkTaskState], Agent]):
        # 工厂后续统一配置子模型、继承权限、工具范围和预算。
        self.jobs = jobs
        self.create_agent = create_agent

    def _result(self, job_id: str) -> ToolResult:
        job = self.jobs.get(job_id)
        task = job.tasks[0]
        return ToolResult(ok=task.status == "completed", output=encode({
            "job_id": job.id, "task_id": task.id, "status": task.status,
            "summary": task.summary, "error": task.error,
        }))

    def execute(self, job_id: str, *, recover_running=False, mark_unknown=False) -> ToolResult:
        job = self.jobs.get(job_id)
        task = job.tasks[0]
        session = self.jobs.get_session(job_id, task.id)
        # 一个子会话同一时间只能有一个执行者。
        with FileLock(str(session.path) + ".execution.lock", timeout=0):
            task = self.jobs.get(job_id).tasks[0]
            if task.status in {"completed", "failed"}:
                return self._result(job_id)
            if task.status == "running":
                if not recover_running:
                    raise ValueError("子任务仍标记为执行中，需先确认中断状态")
                self.jobs.transition(job_id, task.id, status="interrupted", error="原执行者已退出，恢复持久化会话")
                task = self.jobs.get(job_id).tasks[0]
            if mark_unknown:
                branch = session.get_current_branch()
                if branch:
                    session.mark_pending_tools_unknown(expected_leaf_id=branch[-1].id)
            snapshot = self.jobs.get_snapshot(job_id)
            agent = self.create_agent(session, snapshot, task.model_copy(deep=True))
            if not isinstance(agent, Agent) or agent.session_store is not session:
                raise ValueError("子 Agent 必须使用当前任务的独立会话")
            if any((agent.checkpoint_hook, agent.notes_hook, agent.task_started_hook)):
                raise ValueError("子 Agent 不能复用主任务的生命周期钩子")
            if hasattr(agent.model, "before_request"):
                agent.model.before_request = lambda: self.jobs.reserve_request(job_id, task.id)
            self.jobs.transition(job_id, task.id, status="running")
            try:
                # 指令已由 initialize_session 写入，不能再调用 run 重复追加。
                outcome = agent.resume()
                status = outcome["status"]
                if status == "completed":
                    self.jobs.transition(job_id, task.id, status="completed", summary=outcome["answer"])
                elif status in {"needs_recovery", "step_limit"} or outcome.get("error_code") in {"timeout", "connection_error", "rate_limit"}:
                    self.jobs.transition(job_id, task.id, status="interrupted", error=outcome["reason"])
                else:
                    self.jobs.transition(job_id, task.id, status="failed",
                                         error=outcome.get("reason") or "子任务执行失败")
            except BaseException as exc:
                self.jobs.transition(job_id, task.id, status="interrupted",
                                     error=f"执行被中断：{type(exc).__name__}")
                raise
            return self._result(job_id)
