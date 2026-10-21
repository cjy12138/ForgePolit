import json
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.artifacts.store import ArtifactStore
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.summarization.chunking import select_summary_chunk
from forgepilot.sessions.compaction.planning import CompactionPlan
from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.summarization.summarizer import ContextSummarizer
from forgepilot.sessions.summarization.schema import ContextSummary


class SummaryModel:
    max_output_tokens = 100

    def __init__(self):
        self.requests = []

    def estimate_input_tokens(self, messages, tools):
        # 本测试用字节数作为模拟计数，便于制造超限。
        return len(json.dumps(
            {"messages": messages, "tools": tools},
            ensure_ascii=False,
        ).encode("utf-8"))

    def generate(self, messages, tools):
        self.requests.append({
            "messages": messages,
            "tools": tools,
        })

        data = json.loads(messages[1]["content"])
        source_id = data["messages"][-1]["id"]

        summary = {
            name: []
            for name in ContextSummary.model_fields
        }
        summary["completed"] = [{
            "text": "工具执行结束，当前只检查了输出片段。",
            "source_entry_ids": [source_id],
        }]

        return {
            "type": "tool_call",
            "id": "submit_summary",
            "name": "submit_context_summary",
            "arguments": summary,
        }


def main():
    with TemporaryDirectory() as directory:
        artifacts = ArtifactStore(Path(directory))
        text = "输出开头\n" + "中间内容\n" * 30000 + "输出结尾"
        ref = artifacts.save_text(text)

        call = MessageEntry(
            session_id="demo",
            payload={
                "role": "assistant",
                "type": "tool_call",
                "id": "call_large",
                "name": "run_python",
                "arguments": {"args": ["demo.py"]},
            },
        )
        result = MessageEntry(
            session_id="demo",
            parent_id=call.id,
            payload={
                "role": "tool",
                "tool_call_id": "call_large",
                "tool_name": "run_python",
                "content": text,
                "is_error": False,
                "exit_code": 0,
                "artifact": ref.model_dump(),
            },
        )
        kept = MessageEntry(
            session_id="demo",
            parent_id=result.id,
            payload={"role": "user", "content": "继续。"},
        )

        plan = CompactionPlan(
            source_leaf_id=kept.id,
            to_summarize=[call, result],
            to_keep=[kept],
        )
        before = result.model_dump()

        budget = ContextBudget(
            context_window=20200,
            max_output_tokens=100,
            safety_margin=100,
        )

        model = SummaryModel()
        summarizer = ContextSummarizer(
            model,
            context_budget=budget,
            artifact_store=artifacts,
        )

        chunk = select_summary_chunk(plan, summarizer)

        # 调用和结果仍一起进入摘要请求。
        assert chunk == [call, result]

        summary = summarizer.summarize(plan)
        assert len(model.requests) == 1

        sent = model.requests[0]
        data = json.loads(sent["messages"][1]["content"])
        view = data["messages"][1]["message"]

        assert view["snipped"] is True
        assert view["tool_call_id"] == "call_large"
        assert data["messages"][1]["id"] == result.id
        assert len(view["content"]) < len(text)
        print("大工具组缩减后进入预算，调用配对及来源 ID 保留：通过")

        assert any(
            ref.id in item.text
            and item.source_entry_ids == [result.id]
            for item in summary.critical_context
        )
        print("最终摘要包含产物 ID 和未完整查看的提示：通过")

        assert result.model_dump() == before
        assert artifacts.read_text(ref) == text
        print("原始节点和完整产物保持完整：通过")


if __name__ == "__main__":
    main()