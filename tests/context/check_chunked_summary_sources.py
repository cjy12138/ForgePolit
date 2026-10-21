import json
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.artifacts.store import ArtifactStore
from forgepilot.exceptions import ModelError
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.summarization.chunked import ChunkedSummarizer
from forgepilot.sessions.compaction.planning import plan_compaction
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.summarization.summarizer import ContextSummarizer

from tests.context.check_chunked_summarizer import MergeModel
from forgepilot.sessions.context import build_request_messages
from forgepilot.sessions.models import CompactionEntry
from forgepilot.agent import Agent
from forgepilot.contracts import ToolContext
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.artifacts import build_artifact_tools


class DropHintsModel(MergeModel):
    def generate(self, messages, tools):
        response = super().generate(messages, tools)

        # 模拟模型没有保留旧产物提示文字。
        response["arguments"]["critical_context"] = []
        return response

class ReadHistoricalOutputModel:
    def __init__(self, expected_content):
        self.calls = 0
        self.expected_content = expected_content
        self.selected_artifact_id = None

    def generate(self, messages, tools):
        self.calls += 1

        if self.calls == 1:
            summary_message = next(
                message
                for message in messages
                if isinstance(message.get("content"), str)
                and message["content"].startswith(
                    "以下是历史对话的压缩摘要"
                )
            )

            index = json.loads(
                summary_message["content"].split(
                    "\n\n摘要产物来源索引：\n",
                    1,
                )[1]
            )

            self.selected_artifact_id = (
                index["sources"][0]["artifact"]["id"]
            )

            return {
                "type": "tool_call",
                "id": "call_read_history",
                "name": "read_artifact",
                "arguments": {
                    "artifact_id": self.selected_artifact_id,
                    "offset": 5000,
                    "limit": 100,
                },
            }

        if self.calls == 2:
            observation = messages[-1]

            assert observation["role"] == "tool"
            assert observation["tool_call_id"] == "call_read_history"
            assert observation["is_error"] is False

            page = json.loads(observation["content"])

            assert page["offset"] == 5000
            assert page["content"] == self.expected_content

            return {
                "type": "final",
                "content": "已读取压缩前输出的中间片段。",
            }

        raise AssertionError("不应发生额外模型请求")
def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        store = SessionStore(root / "session.jsonl")
        store.create(SessionHeader(
            project_id="demo",
            owner_id="local",
            cwd=directory,
        ))

        artifacts = ArtifactStore(root / "artifacts")
        results = []
        refs = []

        store.append_message({
            "role": "user",
            "content": "检查两个输出",
        })

        for index in range(2):
            call_id = f"call_{index}"
            content = f"第 {index} 份完整输出\n" * 4000
            ref = artifacts.save_text(content)
            refs.append(ref)

            store.append_message({
                "role": "assistant",
                "type": "tool_call",
                "id": call_id,
                "name": "read_file",
                "arguments": {"path": "README.md"},
            })

            results.append(store.append_message({
                "role": "tool",
                "tool_call_id": call_id,
                "tool_name": "read_file",
                "content": content,
                "is_error": False,
                "exit_code": 0,
                "artifact": ref.model_dump(),
            }))

        store.append_message({
            "role": "assistant",
            "type": "final",
            "content": "已读取",
        })

        plan = plan_compaction(
            store.get_current_branch(),
            keep_recent_groups=1,
        )
        assert plan is not None

        # 配合 MergeModel 的模拟估算：
        # 用户消息一块，每个完整工具交互各一块。
        budget = ContextBudget(
            context_window=550,
            max_output_tokens=100,
            safety_margin=100,
        )

        def make_chunked(model):
            return ChunkedSummarizer(
                ContextSummarizer(
                    model,
                    context_budget=budget,
                    artifact_store=artifacts,
                ),
            )

        before = store.path.read_bytes()
        model = DropHintsModel()

        result = make_chunked(model).summarize_with_sources(plan)

        assert model.calls == 3
        assert [
            source.source_entry_id
            for source in result.artifact_sources
        ] == [entry.id for entry in results]

        assert [
            source.artifact
            for source in result.artifact_sources
        ] == refs

        assert all(
            source.has_partial_view
            for source in result.artifact_sources
        )
        print("跨块继承全部产物来源：通过")

        # 最终文字只由程序补回本块的产物提示，
        # 但前面产物的独立元数据没有丢失。
        summary_text = json.dumps(
            result.summary.model_dump(),
            ensure_ascii=False,
        )
        assert refs[0].id not in summary_text
        assert refs[0].id in {
            source.artifact.id
            for source in result.artifact_sources
        }
        print("模型遗漏旧提示，不影响独立来源列表：通过")

        assert store.path.read_bytes() == before

        failed_model = DropHintsModel(fail_at=3)

        try:
            make_chunked(failed_model).summarize_with_sources(plan)
        except ModelError as exc:
            assert exc.code == "fake_failure"
        else:
            raise AssertionError("第三块失败应终止摘要")

        assert failed_model.calls == 3
        assert store.path.read_bytes() == before
        print("中途失败，不返回或保存部分结果：通过")

        compaction = CompactionEntry(
            session_id=plan.to_summarize[0].session_id,
            parent_id=plan.source_leaf_id,
            source_leaf_id=plan.source_leaf_id,
            first_kept_entry_id=plan.first_kept_entry_id,
            summary=result.summary,
            artifact_sources=result.artifact_sources,
        )
        store.append(compaction)

        reopened = SessionStore(store.path)
        saved = store.path.read_bytes()

        request_view = build_request_messages(
            reopened.get_current_branch()
        )

        summary_message = next(
            message
            for message in request_view
            if isinstance(message.get("content"), str)
            and message["content"].startswith(
                "以下是历史对话的压缩摘要"
            )
        )

        marker = "\n\n摘要产物来源索引：\n"
        index = json.loads(
            summary_message["content"].split(marker, 1)[1]
        )

        assert index["type"] == "summary_artifact_index"

        assert [
            source["artifact"]["id"]
            for source in index["sources"]
        ] == [ref.id for ref in refs]

        assert all(
            source["has_partial_view"] is True
            for source in index["sources"]
        )

        # 模型摘要里没有第一份产物的提示，
        # 程序索引仍把它展示到请求中。
        assert refs[0].id not in json.dumps(
            result.summary.model_dump(),
            ensure_ascii=False,
        )
        assert refs[0].id in summary_message["content"]

        assert store.path.read_bytes() == saved
        print("摘要遗漏旧提示，请求仍展示完整产物索引：通过")
        registry = ToolRegistry(build_artifact_tools(
            session_store=reopened,
            artifact_store=artifacts,
        ))

        original_text = artifacts.read_text(refs[0])
        fake_model = ReadHistoricalOutputModel(
            expected_content=original_text[5000:5100],
        )

        agent = Agent(
            model=fake_model,
            tools=registry,
            context=ToolContext(root),
            max_steps=3,
            session_store=reopened,
            artifact_store=artifacts,
        )

        agent_result = agent.run(
            "请读取第一份历史工具输出的中间片段。"
        )

        assert agent_result["status"] == "completed"
        assert agent_result["n_calls"] == 2
        assert fake_model.calls == 2
        assert fake_model.selected_artifact_id == refs[0].id

        # 当前注册表只有 read_artifact。
        # 原来的 read_file 不会因回查历史而重新执行。
        assert [
            tool["name"]
            for tool in registry.definitions()
        ] == ["read_artifact"]

        read_calls = [
            node
            for node in reopened.get_current_branch()
            if getattr(node, "payload", {}).get("type") == "tool_call"
            and node.payload.get("name") == "read_artifact"
        ]

        assert len(read_calls) == 1
        print("Agent 从索引读取被摘要覆盖的输出，不重执行原工具：通过")

        completed_leaf = reopened.replay()[2]

        # 回到原始工具执行之前，创建另一条分支。
        root_message = reopened.get_current_branch()[0]
        reopened.move_cursor(root_message.id)
        reopened.append_message({
            "role": "user",
            "content": "另一条分支的任务",
        })

        saved = reopened.path.read_bytes()

        denied = registry.execute(
            "read_artifact",
            {"artifact_id": refs[0].id},
            context=ToolContext(root),
        )

        assert denied.ok is False
        assert "当前分支没有引用此产物" in denied.output
        assert reopened.path.read_bytes() == saved

        # 文件仍存在，拒绝读取的原因是分支没有引用它。
        assert artifacts.read_text(refs[0]) == original_text
        print("另一分支即使知道产物 ID，也不能读取：通过")

        reopened.move_cursor(completed_leaf)

        allowed = registry.execute(
            "read_artifact",
            {
                "artifact_id": refs[0].id,
                "offset": 5000,
                "limit": 100,
            },
            context=ToolContext(root),
        )

        assert allowed.ok is True
        assert json.loads(allowed.output)["content"] == original_text[5000:5100]
        print("切回原分支后恢复读取范围：通过")


if __name__ == "__main__":
    main()