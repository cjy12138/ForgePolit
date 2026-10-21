from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.agent import Agent
from forgepilot.artifacts.store import ArtifactStore
from forgepilot.contracts import ToolContext
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.control.controller import ContextController
from forgepilot.sessions.models import CompactionEntry, SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.summarization.summarizer import ContextSummarizer
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.artifacts import build_artifact_tools

from tests.context.check_agent_auto_compaction import SequenceEstimateModel
from tests.context.check_session_compactor import FakeSummaryModel


def main():
    cases = [
        ("low", [500], [], 0, 1),
        ("snip", [750, 500], ["snip"], 0, 1),
        ("compact", [750, 730, 200], ["snip", "compact"], 1, 1),
        ("over", [900, 850, 850], None, 1, 0),
    ]

    with TemporaryDirectory() as directory:
        root = Path(directory)

        for name, estimates, actions, summary_calls, business_calls in cases:
            store = SessionStore(root / f"{name}.jsonl")
            store.create(SessionHeader(
                project_id="demo",
                owner_id="local",
                cwd=directory,
            ))
            artifacts = ArtifactStore(root / f"{name}-artifacts")

            store.append_message({
                "role": "user",
                "content": "检查输出，不修改测试文件。",
            })

            for index in range(3):
                text = "完整输出\n" * 4000
                ref = artifacts.save_text(text)

                store.append_message({
                    "role": "assistant",
                    "type": "tool_call",
                    "id": f"call_{index}",
                    "name": "run_python",
                    "arguments": {"args": ["demo.py"]},
                })
                store.append_message({
                    "role": "tool",
                    "tool_call_id": f"call_{index}",
                    "tool_name": "run_python",
                    "content": text,
                    "is_error": False,
                    "exit_code": 0,
                    "artifact": ref.model_dump(),
                })

            before = store.path.read_bytes()
            model = SequenceEstimateModel(estimates)
            summary_model = FakeSummaryModel()

            registry = ToolRegistry(build_artifact_tools(
                session_store=store,
                artifact_store=artifacts,
            ))

            compactor = SessionCompactor(
                store,
                ContextSummarizer(summary_model),
            )

            controller = ContextController(
                model=model,
                tools=registry.definitions(),
                store=store,
                budget=ContextBudget(
                    context_window=1000,
                    max_output_tokens=100,
                    safety_margin=100,
                ),
                artifacts=artifacts,
                compactor=compactor,
                keep_recent_groups=1,
            )

            agent = Agent(
                model,
                registry,
                ToolContext(root),
                session_store=store,
                artifact_store=artifacts,
                context_controller=controller,
            )

            result = agent.resume()

            assert result["n_calls"] == business_calls
            assert len(model.sent_requests) == business_calls
            assert summary_model.calls == summary_calls

            if business_calls:
                assert result["status"] == "completed"
                assert agent.last_context_actions == actions
                assert (
                    model.sent_requests[0]
                    == model.estimated_requests[-1]
                )
            else:
                assert result["status"] == "failed"
                assert result["error_code"] == "context_budget_exceeded"

            compactions = [
                node
                for node in store.get_current_branch()
                if isinstance(node, CompactionEntry)
            ]
            assert len(compactions) == summary_calls
            assert store.path.read_bytes().startswith(before)
            print(f"{name}：通过")


if __name__ == "__main__":
    main()