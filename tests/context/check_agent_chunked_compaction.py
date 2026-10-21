from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.agent import Agent
from forgepilot.contracts import ToolContext
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.summarization.chunked import ChunkedSummarizer
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.models import CompactionEntry
from forgepilot.sessions.summarization.summarizer import ContextSummarizer

from tests.context.check_agent_auto_compaction import SequenceEstimateModel
from tests.context.check_agent_context_budget import NoTools
from tests.context.check_chunked_summarizer import MergeModel
from tests.context.check_session_compactor import make_store


def main():
    business_budget = ContextBudget(
        context_window=1000,
        max_output_tokens=100,
        safety_margin=100,
    )

    cases = [
        # 摘要输入预算 250，两个分块都能完成。
        ("success", 250, None, None, 2),

        # 第二次摘要生成失败。
        ("model_failed", 250, 2, "fake_failure", 2),

        # 第一块需要 200，可以完成；
        # 旧摘要加第二块需要 250，超过 249。
        (
            "next_chunk_too_large",
            249,
            None,
            "summary_group_too_large",
            1,
        ),
    ]

    with TemporaryDirectory() as directory:
        for (
            name,
            summary_input_budget,
            fail_at,
            expected_error,
            expected_summary_calls,
        ) in cases:
            store = make_store(directory, f"{name}.jsonl")
            original_request = store.get_current_branch()[0].payload
            before = store.path.read_bytes()

            summary_budget = ContextBudget(
                context_window=summary_input_budget + 200,
                max_output_tokens=100,
                safety_margin=100,
            )

            summary_model = MergeModel(fail_at=fail_at)

            chunked = ChunkedSummarizer(
                ContextSummarizer(
                    summary_model,
                    context_budget=summary_budget,
                ),
                max_chunks=8,
            )

            compactor = SessionCompactor(store, chunked)

            # 第一次业务估算触发压缩；
            # 只有压缩成功才应进行第二次估算。
            business_model = SequenceEstimateModel(
                [750, 200] if expected_error is None else [750]
            )

            agent = Agent(
                business_model,
                NoTools(),
                ToolContext(Path(directory)),
                session_store=store,
                context_budget=business_budget,
                compactor=compactor,
                keep_recent_groups=1,
            )

            result = agent.resume()

            assert summary_model.calls == expected_summary_calls

            if expected_error is not None:
                assert result["status"] == "failed"
                assert result["error_code"] == expected_error
                assert result["n_calls"] == 0

                assert business_model.sent_requests == []
                assert len(business_model.estimated_requests) == 1
                assert store.path.read_bytes() == before

                print(f"{name}：不保存部分摘要，不请求业务模型：通过")
                continue

            assert result["status"] == "completed"
            assert result["n_calls"] == 1
            assert len(business_model.sent_requests) == 1

            sent = business_model.sent_requests[0]
            assert sent == business_model.estimated_requests[-1]
            assert original_request in sent["messages"]

            compactions = [
                node
                for node in store.get_current_branch()
                if isinstance(node, CompactionEntry)
            ]
            assert len(compactions) == 1

            print("分块压缩成功后重新估算，再继续业务请求：通过")


if __name__ == "__main__":
    main()