from forgepilot.contracts import ToolContext, ToolResult
from forgepilot.fork.executor import ForkExecutor
from forgepilot.fork.models import ForkTasksInput
from forgepilot.fork.service import ForkService
from forgepilot.exceptions import ModelError
from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.validation import pending_tool_calls
from forgepilot.permissions.models import ToolPermissionRequest
import json


class ForkDispatcher:
    def __init__(self, *, service: ForkService, executor: ForkExecutor, parent_model,
                 recovery_permissions=None):
        if executor.jobs is not service.jobs:
            raise ValueError("派发服务与执行器必须使用同一个 Fork 存储")
        self.service = service
        self.executor = executor
        self.parent_model = parent_model
        self.recovery_permissions = recovery_permissions

    def __call__(self, params: ForkTasksInput, context: ToolContext) -> ToolResult:
        if not context.tool_call_id:
            raise ValueError("Fork 派发缺少父工具调用 ID")
        header, _ = self.service.parent_session.read()
        existing = self.service.jobs.find_by_call(header.session_id, context.tool_call_id)
        # 恢复已有作业时复用磁盘快照，不借用父模型最近的其他请求。
        snapshot = None if existing is not None else getattr(self.parent_model, "last_fork_snapshot", None)
        job = self.service.prepare(
            params, context, tool_call_id=context.tool_call_id, snapshot=snapshot,
        )
        result = self.executor.execute(job.id)
        self._require_final_result(result)
        return result

    def _require_final_result(self, result):
        data = json.loads(result.output)
        if data["status"] == "interrupted":
            raise ModelError("fork_interrupted", f"子任务中断，作业 ID：{data['job_id']}。" + (data["error"] or ""))

    def recover_pending(self, context: ToolContext, *, mark_unknown=False) -> list[str]:
        sessions = self.service.parent_session
        branch = sessions.get_current_branch()
        pending = pending_tool_calls(branch)
        calls = {}
        for entry in branch:
            if not isinstance(entry, MessageEntry) or entry.payload.get("role") != "assistant":
                continue
            message = entry.payload
            for call in (message.get("calls", []) if message.get("type") == "tool_batch" else [message]):
                if call.get("id") in pending:
                    calls[call["id"]] = call
        if any(call.get("name") != "fork_tasks" for call in calls.values()) or set(calls) != pending:
            raise ValueError("父会话还存在非 Fork 的未知工具，需先人工检查")
        header, _ = sessions.read()
        restored = []
        for call_id, call in calls.items():
            if self.recovery_permissions is None:
                raise ValueError("Fork 恢复缺少当前父任务权限入口")
            decision = self.recovery_permissions.authorize(ToolPermissionRequest(
                tool_name="fork_tasks", arguments=ForkTasksInput.model_validate(call["arguments"]).model_dump(),
                workspace=str(context.workspace), task=context.task,
            ))
            if decision.action != "allow":
                raise ValueError("Fork 恢复未获当前权限批准：" + decision.reason)
            job = self.service.jobs.find_by_call(header.session_id, call_id)
            if job is None:
                raise ValueError("派发尚未保存 Fork 作业，不能盲目重跑")
            self.service.prepare(ForkTasksInput.model_validate(call["arguments"]), context,
                                 tool_call_id=call_id)
            result = self.executor.execute(job.id, recover_running=True, mark_unknown=mark_unknown)
            self._require_final_result(result)
            sessions.append_message({"role": "tool", "tool_call_id": call_id,
                                     "content": result.output, "is_error": not result.ok, "exit_code": result.exit_code})
            restored.append(job.id)
        return restored
