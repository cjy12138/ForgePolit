from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.agent import Agent
from forgepilot.artifacts.store import ArtifactStore
from forgepilot.contracts import ToolContext
from forgepilot.exceptions import ModelError
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.control.controller import ContextController
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.summarization.summarizer import ContextSummarizer

from tests.runtime.check_agent_resume import FakeTools
from tests.context.check_session_compactor import FakeSummaryModel


class OverflowModel:
    max_output_tokens = 100

    def __init__(self, retry_fails=False):
        self.calls = 0
        self.retry_fails = retry_fails

    def estimate_input_tokens(self, messages, tools):
        has_summary = any(
            message.get("content", "").startswith(
                "以下是历史对话的压缩摘要"
            )
            for message in messages
            if isinstance(message.get("content"), str)
        )
        return 100 if has_summary else 200

    def generate(self, messages, tools):
        self.calls += 1

        if self.calls == 1:
            return {
                "type": "tool_call",
                "id": "call_read",
                "name": "read_file",
                "arguments": {"path": "README.md"},
            }

        if self.calls == 2 or self.retry_fails:
            raise ModelError("context_overflow", "模拟服务端超限")

        return {
            "type": "final",
            "content": "已继续处理。",
        }

def check_recovery_boundaries():
    cases = [
        # 名称、是否配置压缩器、请求上限、预期错误码
        (
            "no_reduction",
            False,
            5,
            "context_overflow_no_reduction",
        ),
        (
            "retry_limit",
            True,
            2,
            "context_overflow_retry_limit",
        ),
    ]

    with TemporaryDirectory() as directory:
        root = Path(directory)

        for name, use_compactor, max_steps, expected_code in cases:
            store = SessionStore(root / f"{name}.jsonl")
            store.create(SessionHeader(
                project_id="demo",
                owner_id="local",
                cwd=directory,
            ))

            artifacts = ArtifactStore(root / f"{name}-artifacts")
            tools = FakeTools()
            model = OverflowModel()
            summary_model = FakeSummaryModel()

            compactor = None
            if use_compactor:
                compactor = SessionCompactor(
                    store,
                    ContextSummarizer(summary_model),
                )

            controller = ContextController(
                model=model,
                tools=tools.definitions(),
                store=store,
                budget=ContextBudget(
                    context_window=1000,
                    max_output_tokens=100,
                    safety_margin=100,
                ),
                artifacts=artifacts,
                compactor=compactor,
                enable_snip=False,
                keep_recent_groups=1,
            )

            agent = Agent(
                model,
                tools,
                ToolContext(root),
                max_steps=max_steps,
                session_store=store,
                artifact_store=artifacts,
                context_controller=controller,
            )

            result = agent.run("读取项目说明。")

            assert result["status"] == "failed"
            assert result["error_code"] == expected_code

            # 第一次请求产生工具调用，第二次请求遇到超限。
            # 两种场景都不应出现第三次请求。
            assert model.calls == 2
            assert result["n_calls"] == 2

            # 已经执行过的工具不能重放。
            assert tools.executions == 1

            # 无压缩器，或请求次数已耗尽，都不应调用摘要模型。
            assert summary_model.calls == 0

            branch = store.get_current_branch()

            # 原始历史保留：用户消息、工具调用、工具结果。
            assert len(branch) == 3
            assert [
                entry.payload["role"]
                for entry in branch
            ] == ["user", "assistant", "tool"]

            assert branch[1].payload["id"] == "call_read"
            assert branch[2].payload["tool_call_id"] == "call_read"

            print(f"{name}：停止恢复，不重发、不重执行工具：通过")

def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)

        for retry_fails in (False, True):
            name = "failed" if retry_fails else "success"
            store = SessionStore(root / f"{name}.jsonl")
            store.create(SessionHeader(
                project_id="demo",
                owner_id="local",
                cwd=directory,
            ))
            artifacts = ArtifactStore(root / f"{name}-artifacts")
            tools = FakeTools()
            model = OverflowModel(retry_fails=retry_fails)
            summary_model = FakeSummaryModel()

            controller = ContextController(
                model=model,
                tools=tools.definitions(),
                store=store,
                budget=ContextBudget(
                    context_window=1000,
                    max_output_tokens=100,
                    safety_margin=100,
                ),
                artifacts=artifacts,
                compactor=SessionCompactor(
                    store,
                    ContextSummarizer(summary_model),
                ),
                enable_snip=False,
                keep_recent_groups=1,
            )

            agent = Agent(
                model,
                tools,
                ToolContext(root),
                max_steps=5,
                session_store=store,
                artifact_store=artifacts,
                context_controller=controller,
            )

            result = agent.run("读取项目说明。")

            assert tools.executions == 1
            assert model.calls == 3
            assert result["n_calls"] == 3
            assert summary_model.calls == 1

            if retry_fails:
                assert result["status"] == "failed"
                assert result["error_code"] == "context_overflow"
            else:
                assert result["status"] == "completed"

            print(f"{name}：最多重试一次，已完成工具不重执行：通过")


if __name__ == "__main__":
    main()
    check_recovery_boundaries()