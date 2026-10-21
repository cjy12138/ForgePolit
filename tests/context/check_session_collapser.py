from pathlib import Path
from tempfile import TemporaryDirectory

from tests.context.check_collapse_selector import FakeSelectionModel
from tests.context.check_session_compactor import FakeSummaryModel

from forgepilot.artifacts.store import ArtifactStore
from forgepilot.exceptions import ModelError
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.budget.summary_job import SummaryJobLimits
from forgepilot.sessions.budget.token_estimator import (
    estimate_request_tokens,
)
from forgepilot.sessions.collapse.collapser import SessionCollapser
from forgepilot.sessions.collapse.selector import CollapseSelector
from forgepilot.sessions.collapse.summarizer import CollapseSummarizer
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.summarization.chunked import ChunkedSummarizer
from forgepilot.sessions.summarization.summarizer import ContextSummarizer


class SummaryModel(FakeSummaryModel):
    max_output_tokens = 512

    def estimate_input_tokens(self, *, messages, tools):
        return estimate_request_tokens({
            "messages": messages,
            "tools": tools,
        })


def main():
    for mode in ("success", "skip", "insufficient", "request_limit"):
        with TemporaryDirectory() as directory:
            store = SessionStore(
                Path(directory) / "session.jsonl"
            )
            store.create(SessionHeader(
                project_id="demo",
                owner_id="local",
                cwd=directory,
            ))

            store.append_message({
                "role": "user",
                "content": "检查项目。",
            })

            for index in range(6):
                call_id = f"call_{index}"

                store.append_message({
                    "role": "assistant",
                    "type": "tool_call",
                    "id": call_id,
                    "name": "read_file",
                    "arguments": {"path": "calculator.py"},
                })
                store.append_message({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": f"输出 {index}\n" + "x" * 12000,
                    "is_error": False,
                    "exit_code": 0,
                })

            budget = ContextBudget(
                context_window=32000,
                max_output_tokens=512,
                safety_margin=1000,
            )

            selection_model = FakeSelectionModel(
                "skip" if mode == "skip" else "select"
            )
            summary_model = SummaryModel()

            collapser = SessionCollapser(
                store=store,
                selector=CollapseSelector(
                    selection_model,
                    context_budget=budget,
                ),
                summarizer=CollapseSummarizer(
                    ChunkedSummarizer(
                        ContextSummarizer(
                            summary_model,
                            context_budget=budget,
                        )
                    )
                ),
                model=summary_model,
                tools=[],
                artifacts=ArtifactStore(
                    Path(directory) / "artifacts"
                ),
                job_limits=SummaryJobLimits(
                    max_requests=(
                        1 if mode == "request_limit" else 8
                    ),
                    max_reserved_tokens=200000,
                ),
                minimum_savings_tokens=(
                    1000000 if mode == "insufficient" else 256
                ),
            )

            original = store.path.read_bytes()
            original_branch = store.get_current_branch()

            try:
                if mode == "success":
                    prepared = collapser.prepare_candidate(
                        branch=store.get_current_branch(),
                        records=store.get_current_collapse_records(),
                        snip_records=[],
                        enable_snip=False,
                    )

                    assert prepared is not None
                    assert store.path.read_bytes() == original
                    assert store.get_current_collapse_records() == []
                    print("准备候选不写入会话：通过")
                    prepared_leaf_id = prepared.record.source_leaf_id

                    added = store.append_message({
                        "role": "assistant",
                        "type": "final",
                        "content": "完成本轮检查。",
                    })

                    # 后续消息树检查以追加后的分支为准。
                    original_branch = store.get_current_branch()
                    record = collapser.publish_candidate(
                        prepared,
                        enable_snip=False,
                    )
                    assert record is not None
                    assert record.prepared_leaf_id == prepared_leaf_id
                    assert record.source_leaf_id == added.id
                    assert prepared.record.source_leaf_id == prepared_leaf_id

                    reopened = SessionStore(store.path)
                    assert reopened.get_current_collapse_records() == [record]
                    print("尾部新增消息后重新校验并发布：通过")

                else:
                    record = collapser.collapse(
                        enable_snip=False
                    )
            except ModelError as exc:
                expected = {
                    "insufficient": "collapse_insufficient_savings",
                    "request_limit": "summary_job_request_limit",
                }
                assert exc.code == expected[mode]
                assert store.path.read_bytes() == original

                if mode == "request_limit":
                    assert summary_model.calls == 0

                print(f"{mode}：拒绝发布，历史不变：通过")
                continue

            if mode == "skip":
                assert record is None
                assert summary_model.calls == 0
                assert store.path.read_bytes() == original
                print("模型跳过时不生成摘要：通过")
            else:
                assert record is not None
                assert store.get_current_collapse_records() == [record]
                assert collapser.last_savings_tokens >= 256
                assert collapser.last_job_budget.requests == 2
                assert store.get_current_branch() == original_branch
                print("选择和摘要共享预算，有收益才发布：通过")

                # 同一个候选不能再次覆盖已经折叠的范围。
                saved = store.path.read_bytes()

                assert collapser.publish_candidate(
                    prepared,
                    enable_snip=False,
                ) is None

                assert store.path.read_bytes() == saved
                print("已有折叠范围冲突时丢弃候选：通过")

                # 切回来源锚点之前，再创建另一条分支。
                root_id = store.get_current_branch()[0].id
                store.move_cursor(root_id)
                store.append_message({
                    "role": "user",
                    "content": "开始另一项任务。",
                })

                saved = store.path.read_bytes()

                assert collapser.publish_candidate(
                    prepared,
                    enable_snip=False,
                ) is None

                assert store.path.read_bytes() == saved
                print("来源锚点不在当前分支时丢弃候选：通过")


if __name__ == "__main__":
    main()
