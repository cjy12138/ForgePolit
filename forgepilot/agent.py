from pydantic import ValidationError

from forgepilot.contracts import response_adapter
from forgepilot.exceptions import ModelError
from forgepilot.sessions.context import build_request_messages
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.store import SessionStore
from copy import deepcopy

from forgepilot.sessions.validation import pending_tool_calls
from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.artifacts.store import ArtifactStore
from forgepilot.sessions.snip.request_view import build_snipped_request
from forgepilot.sessions.control.controller import ContextController
from collections.abc import Callable
from dataclasses import replace

class Agent:
    def __init__(
            self,
            model,
            tools,
            context,
            max_steps=5,
            *,
            session_store: SessionStore | None = None,
            context_budget: ContextBudget | None = None,
            compactor: SessionCompactor | None = None,
            keep_recent_groups: int = 3,
            artifact_store: ArtifactStore | None = None,
            enable_snip: bool = False,
            context_controller: ContextController | None = None,
            checkpoint_hook: Callable[[], object] | None = None,
            notes_hook: Callable[[], dict] | None = None,
            task_started_hook: Callable[[], None] | None = None,
    ):
        self.model = model
        self.max_steps = max_steps
        self.tools = tools
        self.tool_definitions = tools.definitions()
        self.context = context
        self.session_store = session_store

        self.messages = []
        self.n_calls = 0
        self.context_budget = context_budget

        if context_budget is not None:
            if not callable(
                getattr(model, "estimate_input_tokens", None)
            ):
                raise ValueError("启用预算需要模型支持输入估算")

            if (
                getattr(model, "max_output_tokens", None)
                != context_budget.max_output_tokens
            ):
                raise ValueError("模型输出上限与预算预留不一致")

        if (
            type(keep_recent_groups) is not int
            or keep_recent_groups < 1
        ):
            raise ValueError("keep_recent_groups 必须是正整数")

        if compactor is not None:
            if session_store is None or context_budget is None:
                raise ValueError("自动压缩需要会话存储和预算配置")

            if compactor.store is not session_store:
                raise ValueError("Agent 和压缩器必须使用同一个存储对象")

        self.compactor = compactor
        self.keep_recent_groups = keep_recent_groups

        self.artifact_store = artifact_store

        if type(enable_snip) is not bool:
            raise ValueError("enable_snip 必须是布尔值")

        if enable_snip:
            if session_store is None or self.artifact_store is None:
                raise ValueError("Snip 需要会话和产物存储")

            tool_names = {
                tool["name"]
                for tool in self.tool_definitions
            }

            if "read_artifact" not in tool_names:
                raise ValueError("启用 Snip 前必须注册 read_artifact")

        self.enable_snip = enable_snip

        if self.artifact_store is None and session_store is not None:
            self.artifact_store = ArtifactStore(
                session_store.path.parent
                / "artifacts"
                / session_store.path.stem
            )

        if context_controller is not None:
            if context_controller.store is not session_store:
                raise ValueError("Agent 与控制器必须使用同一个会话存储")

            if context_controller.model is not model:
                raise ValueError("Agent 与控制器必须使用同一个业务模型")

            if context_controller.tools != self.tool_definitions:
                raise ValueError("Agent 与控制器的工具定义不一致")

            if context_controller.artifacts is not self.artifact_store:
                raise ValueError("Agent 与控制器必须使用同一个产物存储")

            if (
                    context_budget is not None
                    or compactor is not None
                    or enable_snip
            ):
                raise ValueError("使用控制器时，请将上下文策略配置在控制器中")

        self.context_controller = context_controller
        self.last_context_actions = []
        if checkpoint_hook is not None:
            if not callable(checkpoint_hook):
                raise ValueError("checkpoint_hook 必须可调用")

            if session_store is None:
                raise ValueError("自动检查点需要会话存储")

        self.checkpoint_hook = checkpoint_hook

        if notes_hook is not None:
            if not callable(notes_hook):
                raise ValueError("notes_hook 必须可调用")

            if session_store is None:
                raise ValueError("自动会话笔记需要会话存储")

        self.notes_hook = notes_hook
        self.last_notes_result = None
        if task_started_hook is not None:
            if not callable(task_started_hook):
                raise ValueError("任务开始钩子必须可调用")

            if session_store is None:
                raise ValueError("任务开始钩子需要会话存储")

        self.task_started_hook = task_started_hook

    def query(self):
        self.last_context_actions = []
        previous_estimate = None

        if self.context_controller is not None:
            prepared = self.context_controller.prepare()
            self.messages = prepared.messages
            self.last_context_actions = prepared.actions
            previous_estimate = prepared.decision.input_tokens
        else:
            if self.enable_snip:
                self.messages = self.build_context_view(
                    self.session_store.get_current_branch()
                )

            self.check_context_budget()

        try:
            raw_response = self.request_model()
        except ModelError as exc:
            if (
                    exc.code != "context_overflow"
                    or self.context_controller is None
            ):
                raise

            if self.n_calls >= self.max_steps:
                raise ModelError(
                    "context_overflow_retry_limit",
                    "服务端上下文超限，但业务请求次数已达上限。",
                ) from exc

            prepared = self.context_controller.prepare(
                force_compaction=True,
                restore_files=False,
            )

            if (
                    prepared.messages == self.messages
                    or prepared.decision.input_tokens >= previous_estimate
            ):
                raise ModelError(
                    "context_overflow_no_reduction",
                    "未准备出更小的请求，停止重复发送。",
                ) from exc

            self.messages = prepared.messages
            self.last_context_actions.extend(prepared.actions)
            self.last_context_actions.append("overflow_retry")

            # 第二次失败直接向上传递，不再进入恢复循环。
            raw_response = self.request_model()

        validated = response_adapter.validate_python(raw_response)
        response = validated.model_dump()

        self.record_message({
            "role": "assistant",
            **response,
        })

        return response

    def get_permission_task(self) -> str:
        if self.session_store is not None:
            # 使用当前分支的原始记录，不使用压缩后的请求视图。
            entries = self.session_store.get_current_branch()

            for entry in reversed(entries):
                if not isinstance(entry, MessageEntry):
                    continue

                message = entry.payload

                if message.get("role") == "user":
                    content = message.get("content")

                    if not isinstance(content, str):
                        raise ValueError("用户任务必须是字符串")

                    return content

        else:
            # 没有持久会话时，使用内存中的消息。
            for message in reversed(self.messages):
                if message.get("role") == "user":
                    content = message.get("content")

                    if not isinstance(content, str):
                        raise ValueError("用户任务必须是字符串")

                    return content

        return ""

    
    def execute_actions(self, response):
        # 1. 调用注册表，传入工具名和参数。
        permission_context = replace(
            self.context,
            task=self.get_permission_task(),
            tool_call_id=response["id"],
        )

        result = self.tools.execute(
            response["name"],
            response["arguments"],
            context=permission_context,
        )

        observation = {
            "role": "tool",
            "tool_call_id": response["id"],
            "content": result.output,
            "is_error": not result.ok,
            "exit_code": result.exit_code,
        }

        if self.artifact_store is not None:
            ref = self.artifact_store.save_text(result.output)

            observation["artifact"] = ref.model_dump()
            observation["tool_name"] = response["name"]

        self.record_message(observation)
        return observation


    def step(self):
        try:
            response = self.query()
        except ValidationError as exc:
            return {
                "status": "failed",
                "reason": f"模型响应格式不合法：{exc}",
            }
        except ModelError as exc:
            return {
                "status": "failed",
                "error_code": exc.code,
                "reason": str(exc),
            }

        if response["type"] == "final":
            return {
                "status": "completed",
                "answer": response["content"],
            }

        if response["type"] == "tool_call":
            self.execute_actions(response)
            return {"status": "running"}

        if response["type"] == "tool_batch":
            # 整批响应已在 query 中校验；顺序执行，逐个保存结果。
            for call in response["calls"]:
                self.execute_actions(call)
            return {"status": "running"}

        return {
            "status": "failed",
            "reason": "未知的模型响应类型",
        }

    def run(self, user_input):
        # 每次调用重置本次请求计数；有会话时加载历史。
        self.n_calls = 0
        if callable(getattr(self.tools, "begin_turn", None)):
            self.tools.begin_turn()

        if self.session_store is None:
            self.messages = []
        else:
            self.messages = self.build_context_view(
                self.session_store.get_current_branch()
            )

        self.record_message({
            "role": "user",
            "content": user_input,
        })

        if self.task_started_hook is not None:
            self.task_started_hook()

        return self._run_loop()

    def _run_loop(self):
        try:
            # run() 已保存用户消息；
            # resume() 已检查没有待完成工具。
            # 在首次模型请求前保存当前代码。
            self.save_checkpoint()

            while self.n_calls < self.max_steps:
                result = self.step()

                if result["status"] in {"running", "completed"}:
                    # step() 返回 running 时，
                    # 单个工具或整批工具的结果已经保存完毕。
                    self.save_checkpoint()
                    self.update_session_notes()

                if result["status"] != "running":
                    return {
                        **result,
                        "messages": self.messages,
                        "n_calls": self.n_calls,
                    }

        except ModelError as exc:
            return {
                "status": "failed",
                "error_code": exc.code,
                "reason": str(exc),
                "messages": self.messages,
                "n_calls": self.n_calls,
            }

        return {
            "status": "step_limit",
            "reason": (
                f"已达到 {self.max_steps} 次模型请求上限"
            ),
            "messages": self.messages,
            "n_calls": self.n_calls,
        }
    def resume(self):
        if self.session_store is None:
            raise ValueError("恢复执行需要提供会话存储")

        self.n_calls = 0
        if callable(getattr(self.tools, "begin_turn", None)):
            self.tools.begin_turn()

        entries = self.session_store.get_current_branch()
        pending = pending_tool_calls(entries)

        if pending:
            # 仅用于展示中断现场，不发送给模型。
            self.messages = [
                deepcopy(entry.payload)
                for entry in entries
                if isinstance(entry, MessageEntry)
            ]

            return {
                "status": "needs_recovery",
                "reason": (
                    "部分工具调用缺少已保存的结果，"
                    "实际执行情况未知，需要先处理中断状态。"
                ),
                "pending_call_ids": sorted(pending),
                "messages": self.messages,
                "n_calls": self.n_calls,
            }

        messages = self.build_context_view(entries)

        if not messages:
            raise ValueError("当前会话没有可恢复的消息")

        last_message = messages[-1]

        if last_message["role"] == "system":
            raise ValueError("当前会话尚无可恢复的任务")

        self.messages = messages

        if (
                last_message["role"] == "assistant"
                and last_message["type"] == "final"
        ):
            self.update_session_notes()
            return {
                "status": "completed",
                "answer": last_message["content"],
                "messages": self.messages,
                "n_calls": self.n_calls,
            }

        return self._run_loop()

    def record_message(self,message):
        if self.session_store is not None:
            self.session_store.append_message(message)

        self.messages.append(message)

    def check_context_budget(self):
        if self.context_budget is None:
            return None

        estimated = self.model.estimate_input_tokens(
            messages=self.messages,
            tools=self.tool_definitions,
        )
        decision = self.context_budget.assess(estimated)

        if decision.should_compact and self.compactor is not None:
            # 每次业务请求前最多尝试一次，不在这里循环压缩。
            entry = self.compactor.compact(
                keep_recent_groups=self.keep_recent_groups,
            )

            if entry is not None:
                # 保存压缩节点不会自动改变内存中的 self.messages。
                # 必须重新从会话构造请求。
                self.messages = self.build_context_view(
                    self.session_store.get_current_branch()
                )

                estimated = self.model.estimate_input_tokens(
                    messages=self.messages,
                    tools=self.tool_definitions,
                )
                decision = self.context_budget.assess(estimated)

        if decision.exceeds_budget:
            raise ModelError(
                "context_budget_exceeded",
                (
                    f"估算输入为 {decision.input_tokens} Token，"
                    f"超过输入预算 {decision.input_budget} Token。"
                    "本次业务请求未发送。"
                ),
            )

        return decision

    def build_context_view(self, entries):
        if self.context_controller is not None:
            return self.context_controller.build_context_view(entries)
        if self.enable_snip:
            return build_snipped_request(
                entries,
                self.artifact_store,
                keep_recent_tool_groups=2,
            )

        return build_request_messages(entries)

    def request_model(self):
        self.n_calls += 1

        return self.model.generate(
            messages=self.messages,
            tools=self.tool_definitions,
        )

    def save_checkpoint(self) -> None:
        if self.checkpoint_hook is None:
            return

        try:
            self.checkpoint_hook()
        except (ValueError, RuntimeError, OSError) as exc:
            raise ModelError(
                "checkpoint_failed",
                f"自动保存代码检查点失败：{exc}",
            ) from exc

    def update_session_notes(self) -> None:
        if self.notes_hook is None:
            return

        try:
            self.last_notes_result = self.notes_hook()

        except (ModelError, ValueError, OSError) as exc:
            self.last_notes_result = {
                "status": "failed",
                "reason": str(exc),
            }
