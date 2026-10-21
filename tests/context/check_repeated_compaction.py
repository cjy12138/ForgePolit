import json
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.artifacts.store import ArtifactStore
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.summarization.chunked import ChunkedSummarizer
from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.context import build_request_messages
from forgepilot.sessions.models import CompactionEntry, SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.summarization.summarizer import ContextSummarizer

from tests.context.check_chunked_summary_sources import DropHintsModel


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
        refs = []
        outputs = []
        result_entries = []

        original_request = (
            "修复 calculator.py。"
            "只修改 calculator.py，不修改 test_calculator.py。"
        )

        store.append_message({
            "role": "user",
            "content": original_request,
        })

        def append_tool_group(index, content):
            call_id = f"call_{index}"
            ref = artifacts.save_text(content)

            refs.append(ref)
            outputs.append(content)

            store.append_message({
                "role": "assistant",
                "type": "tool_call",
                "id": call_id,
                "name": "read_file",
                "arguments": {"path": "README.md"},
            })

            result_entries.append(store.append_message({
                "role": "tool",
                "tool_call_id": call_id,
                "tool_name": "read_file",
                "content": content,
                "is_error": False,
                "exit_code": 0,
                "artifact": ref.model_dump(),
            }))

        budget = ContextBudget(
            context_window=550,
            max_output_tokens=100,
            safety_margin=100,
        )

        def make_compactor(current_store):
            return SessionCompactor(
                current_store,
                ChunkedSummarizer(
                    ContextSummarizer(
                        DropHintsModel(),
                        context_budget=budget,
                        artifact_store=artifacts,
                    ),
                ),
            )

        # 第一轮压缩：覆盖第一份大输出，保留最后回复。
        append_tool_group(0, "第一份完整输出\n" * 4000)

        store.append_message({
            "role": "assistant",
            "type": "final",
            "content": "第一阶段已读取。",
        })

        first = make_compactor(store).compact(
            keep_recent_groups=1,
        )

        assert first is not None
        assert first.artifact_sources_version == 2
        assert [
            source.artifact.id
            for source in first.artifact_sources
        ] == [refs[0].id]
        print("第一轮保存第一份产物来源：通过")

        # 重新打开，再继续产生历史。
        store = SessionStore(store.path)

        followup = "继续验证，仍然不允许修改测试文件。"
        store.append_message({
            "role": "user",
            "content": followup,
        })

        append_tool_group(1, "第二份完整输出\n" * 4000)
        append_tool_group(2, "近期工具结果，必须完整保留。")

        raw_before = {
            node.id: node.model_dump()
            for node in store.get_current_branch()
        }

        second = make_compactor(store).compact(
            keep_recent_groups=1,
        )

        assert second is not None
        assert [
            source.artifact.id
            for source in second.artifact_sources
        ] == [refs[0].id, refs[1].id]

        assert all(
            source.has_partial_view
            for source in second.artifact_sources
        )
        print("第二轮继承旧来源并新增来源：通过")

        store = SessionStore(store.path)
        branch = store.get_current_branch()
        saved = store.path.read_bytes()
        view = build_request_messages(branch)

        # 被摘要覆盖的原始用户要求仍独立保留。
        assert any(
            message.get("role") == "user"
            and message.get("content") == original_request
            for message in view
        )
        assert any(
            message.get("role") == "user"
            and message.get("content") == followup
            for message in view
        )
        print("连续压缩后仍保留两轮原始限制：通过")

        summaries = [
            message
            for message in view
            if isinstance(message.get("content"), str)
            and message["content"].startswith(
                "以下是历史对话的压缩摘要"
            )
        ]
        assert len(summaries) == 1

        index = json.loads(
            summaries[0]["content"].split(
                "\n\n摘要产物来源索引：\n",
                1,
            )[1]
        )
        assert [
            source["artifact"]["id"]
            for source in index["sources"]
        ] == [refs[0].id, refs[1].id]

        visible_calls = [
            message["id"]
            for message in view
            if message.get("type") == "tool_call"
        ]
        visible_results = [
            message["tool_call_id"]
            for message in view
            if message.get("role") == "tool"
        ]

        assert visible_calls == ["call_2"]
        assert visible_results == ["call_2"]

        recent_result = next(
            message
            for message in view
            if message.get("role") == "tool"
        )
        assert recent_result["content"] == outputs[2]
        assert "snipped" not in recent_result
        print("只展示最新摘要，近期工具交互完整，旧输出不展开：通过")

        by_id = {node.id: node for node in branch}

        for node_id, original in raw_before.items():
            assert by_id[node_id].model_dump() == original

        for ref, output in zip(refs, outputs, strict=True):
            assert artifacts.read_text(ref) == output

        assert len([
            node
            for node in branch
            if isinstance(node, CompactionEntry)
        ]) == 2

        assert store.path.read_bytes() == saved
        print("原始历史与完整产物不变，生成视图不写文件：通过")


if __name__ == "__main__":
    main()