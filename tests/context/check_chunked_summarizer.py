import json
from copy import deepcopy
from tempfile import TemporaryDirectory

from forgepilot.exceptions import ModelError
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.summarization.chunked import ChunkedSummarizer
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.models import CompactionEntry
from forgepilot.sessions.summarization.summarizer import ContextSummarizer
from forgepilot.sessions.summarization.schema import ContextSummary

from tests.context.check_session_compactor import make_store
from forgepilot.sessions.control.stats import ContextStats

class MergeModel:
    max_output_tokens = 100

    def __init__(self, fail_at=None):
        self.fail_at = fail_at
        self.calls = 0
        self.inputs = []

    def estimate_input_tokens(self, messages, tools):
        data = json.loads(messages[1]["content"])

        # 模拟固定开销、旧摘要开销和本块历史开销。
        previous_cost = 50 if data["previous_summary"] else 0
        return (
            100
            + previous_cost
            + 100 * len(data["messages"])
        )

    def generate(self, messages, tools):
        self.calls += 1

        if self.calls == self.fail_at:
            raise ModelError("fake_failure", "模拟中途失败")

        data = json.loads(messages[1]["content"])
        self.inputs.append(deepcopy(data))

        summary = data["previous_summary"]

        if summary is None:
            summary = {
                name: []
                for name in ContextSummary.model_fields
            }

        for entry in data["messages"]:
            summary["critical_context"].append({
                "text": "保留此消息中的历史信息。",
                "source_entry_ids": [entry["id"]],
            })

        return {
            "type": "tool_call",
            "id": f"summary_{self.calls}",
            "name": "submit_context_summary",
            "arguments": summary,
        }


def main():
    # 输入预算 250：
    # 首块一条消息需要 200；
    # 有旧摘要时一条需要 250，两条需要 350。
    budget = ContextBudget(
        context_window=450,
        max_output_tokens=100,
        safety_margin=100,
    )

    cases = [
        ("success", None, 8, None),
        ("failed", 2, 8, "fake_failure"),
        ("limited", None, 1, "summary_chunk_limit"),
    ]

    with TemporaryDirectory() as directory:
        for name, fail_at, max_chunks, expected_error in cases:
            store = make_store(directory, f"{name}.jsonl")
            original_branch = store.get_current_branch()
            before = store.path.read_bytes()

            model = MergeModel(fail_at=fail_at)
            stats = ContextStats()
            chunked = ChunkedSummarizer(
                ContextSummarizer(
                    model,
                    context_budget=budget,
                    stats=stats,
                ),
                max_chunks=max_chunks,
            )

            compactor = SessionCompactor(store, chunked)

            if expected_error is not None:
                try:
                    compactor.compact(keep_recent_groups=1)
                except ModelError as exc:
                    assert exc.code == expected_error
                else:
                    raise AssertionError("应停止本次摘要作业")

                assert store.path.read_bytes() == before
                assert model.calls == (2 if name == "failed" else 1)
                assert stats.summary_requests == model.calls
                print(f"{name}：中途停止，不保存部分摘要：通过")
                continue

            entry = compactor.compact(keep_recent_groups=1)

            assert entry is not None
            assert model.calls == 2
            assert stats.summary_requests == 2
            assert model.inputs[0]["previous_summary"] is None
            assert model.inputs[1]["previous_summary"] is not None

            # 两条待压缩消息依次处理，没有漏掉或重复。
            processed = [
                message["id"]
                for data in model.inputs
                for message in data["messages"]
            ]
            expected_ids = [
                node.id
                for node in original_branch[:-1]
            ]
            assert processed == expected_ids

            sources = {
                source_id
                for item in entry.summary.critical_context
                for source_id in item.source_entry_ids
            }
            assert sources == set(expected_ids)

            compactions = [
                node
                for node in store.get_current_branch()
                if isinstance(node, CompactionEntry)
            ]
            assert len(compactions) == 1
            assert store.path.read_bytes().startswith(before)
            print("多块合并完成后只保存一个压缩节点：通过")


if __name__ == "__main__":
    main()