import json
import warnings
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.contracts import ToolContext
from forgepilot.memory.models import MemoryDocument, MemoryMetadata
from forgepilot.memory.recall import MemoryRecall, clip_body
from forgepilot.memory.selector import MemorySelector
from forgepilot.memory.store import MarkdownMemoryStore
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.memory import build_memory_tools


class SelectionModel:
    max_output_tokens = 512

    def __init__(self, names):
        self.names = names
        self.calls = 0
        self.request = None

    def estimate_input_tokens(self, *, messages, tools):
        return 100

    def generate(self, *, messages, tools):
        self.calls += 1
        self.request = json.loads(messages[1]["content"])
        return {"type": "tool_call", "id": "choice", "name": "submit_memory_choice",
                "arguments": {"filenames": self.names}}


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        memory = MarkdownMemoryStore(root / "memory")
        memory.save(MemoryDocument(
            filename="deploy_flow.md",
            metadata=MemoryMetadata(
                name="发布流程", type="project", description="发布必须经过 release 分支",
                source_session_id="source", source_entry_ids=["entry"],
            ),
            content="正文独有标记\n" + "部署细节\n" * 300,
        ))
        # 正文含非法 UTF-8，扫描 header 仍可成功，证明没有解码正文。
        path = memory.root / "header_only.md"
        header = (memory.root / "deploy_flow.md").read_bytes().split(b"\n---\n", 1)[0]
        path.write_bytes(header + b"\n---\n\xff")
        (memory.root / "broken.md").write_text("---\n" + "# padding\n" * 30 + "---\n", encoding="utf-8")
        candidates, skipped = memory.scan_headers()
        assert {item["filename"] for item in candidates} == {"deploy_flow.md", "header_only.md"}
        assert skipped[0]["filename"] == "broken.md"
        print("只扫描完整 header，跳过超长 header：通过")

        budget = ContextBudget(context_window=32000, max_output_tokens=512, safety_margin=2000)
        model = SelectionModel(["deploy_flow.md"])
        selector = MemorySelector(model, context_budget=budget)
        sessions = SessionStore(root / "session.jsonl")
        sessions.create(SessionHeader(owner_id="user", project_id="project", cwd=str(root)))
        sessions.append_message({"role": "user", "content": "我在调试部署问题"})
        state_path = root / "recall.json"
        recall = MemoryRecall(memory_store=memory, selector=selector, sessions=sessions, state_path=state_path)
        history_before = sessions.path.read_bytes()
        prefix = recall()
        data = json.loads(prefix[0]["content"].split("\n", 1)[1])
        document = data["memories"][0]
        assert document["filename"] == "deploy_flow.md"
        assert document["truncated"]
        assert len(document["content"].splitlines()) <= 200
        assert len(document["content"].encode("utf-8")) <= 4096
        assert "正文独有标记" not in model.request["manifest"]
        assert "发布必须经过 release 分支" in model.request["manifest"]
        assert model.request["question"] == "我在调试部署问题"
        assert sessions.path.read_bytes() == history_before
        used = json.loads(state_path.read_text(encoding="utf-8"))["used_bytes"]
        assert recall() == prefix and model.calls == 1
        reopened = MemoryRecall(memory_store=memory, selector=selector, sessions=sessions, state_path=state_path)
        assert reopened() == prefix and model.calls == 1
        assert json.loads(state_path.read_text(encoding="utf-8"))["used_bytes"] == used
        print("模型只看 manifest，按需读正文；同轮与恢复复用且不改历史：通过")

        for names in (["../private.md"], ["deploy_flow.md"] * 2, ["deploy_flow.md"] * 6):
            model.names = names
            try:
                selector.select(question="部署", candidates=candidates, recent_tools=[])
            except ValueError:
                pass
            else:
                raise AssertionError("应拒绝非法选择")
        model.names = []
        assert selector.select(question="无关问题", candidates=candidates, recent_tools=[]) == []
        print("允许空选择，拒绝越界、重复和超过五个文件：通过")

        sessions.append_message({"role": "assistant", "type": "final", "content": "完成"})
        sessions.append_message({"role": "user", "content": "新的任务"})
        model.names = ["missing.md"]
        before = model.calls
        with warnings.catch_warnings(record=True) as notices:
            assert recall() == []
            assert recall() == []
        assert notices and model.calls == before + 1
        print("召回失败不阻断，不在工具循环中重复请求：通过")

        # 主动补读与自动召回共享累计预算，并对中文按字节截断。
        content, truncated = clip_body("中" * 3000, 4096)
        assert truncated and len(content.encode("utf-8")) <= 4096
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["used_bytes"] = 60 * 1024 - 8
        state_path.write_text(json.dumps(state), encoding="utf-8")
        registry = ToolRegistry(build_memory_tools(memory_store=memory, consume_read=recall.consume_tool_read))
        result = registry.execute("read_memory", {"filename": "deploy_flow.md"}, context=ToolContext(workspace=root))
        assert result.ok
        output = json.loads(result.output)
        assert len(output["content"].encode("utf-8")) <= 8
        assert output["next_offset"] == len(output["content"])
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["used_bytes"] = 60 * 1024
        state_path.write_text(json.dumps(state), encoding="utf-8")
        result = registry.execute("read_memory", {"filename": "deploy_flow.md"}, context=ToolContext(workspace=root))
        assert not result.ok
        print("中文字节截断、补读偏移和会话累计上限：通过")


if __name__ == "__main__":
    main()
