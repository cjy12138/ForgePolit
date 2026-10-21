from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.agent import Agent
from forgepilot.artifacts.store import ArtifactRef
from forgepilot.contracts import ToolContext
from forgepilot.sessions.integrity import check_session_integrity

from tests.tools.check_tool_artifacts import (
    LargeOutputTools,
    OneCallModel,
    make_store,
)


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        store = make_store(directory, "integrity")

        agent = Agent(
            OneCallModel(),
            LargeOutputTools(),
            ToolContext(root),
            max_steps=1,
            session_store=store,
        )
        agent.run("生成输出。")

        before = store.path.read_bytes()
        report = check_session_integrity(
            store,
            agent.artifact_store,
        )

        assert report.message_count == 3
        assert report.compaction_count == 0
        assert report.artifact_count == 1
        assert store.path.read_bytes() == before
        print("重新读取并检查会话及完整产物，不修改文件：通过")

        store.append_message({
            "role": "assistant",
            "type": "tool_call",
            "id": "pending_call",
            "name": "run_python",
            "arguments": {"args": ["demo.py"]},
        })

        try:
            check_session_integrity(store, agent.artifact_store)
        except ValueError as exc:
            assert "未完成" in str(exc)
        else:
            raise AssertionError("缺少工具结果不能算完整")

        print("工具结果缺失时拒绝完整性通过：通过")

        store.mark_pending_tools_unknown(
            expected_leaf_id=store.get_current_branch()[-1].id,
        )

        # 明确记录未知结果后，协议配对完整；
        # 这不表示工具实际执行成功。
        check_session_integrity(store, agent.artifact_store)

        payload = store.get_current_branch()[2].payload
        ref = ArtifactRef.model_validate(payload["artifact"])

        path = agent.artifact_store.root / f"{ref.id}.txt"
        path.write_bytes(b"damaged")

        try:
            check_session_integrity(store, agent.artifact_store)
        except ValueError:
            pass
        else:
            raise AssertionError("产物损坏时不能通过")

        print("产物损坏时拒绝完整性通过：通过")


if __name__ == "__main__":
    main()