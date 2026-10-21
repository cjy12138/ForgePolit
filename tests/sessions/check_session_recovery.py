import json
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.sessions.context import build_messages
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.validation import pending_tool_calls


def main():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "session.jsonl"
        store = SessionStore(path)

        store.create(SessionHeader(
            project_id="demo-project",
            owner_id="demo-user",
            cwd=directory,
        ))

        store.append_message({
            "role": "user",
            "content": "读取两个文件",
        })

        batch = store.append_message({
            "role": "assistant",
            "type": "tool_batch",
            "calls": [
                {
                    "type": "tool_call",
                    "id": "call_a",
                    "name": "read_file",
                    "arguments": {"path": "a.py"},
                },
                {
                    "type": "tool_call",
                    "id": "call_b",
                    "name": "read_file",
                    "arguments": {"path": "b.py"},
                },
            ],
        })

        result_a = store.append_message({
            "role": "tool",
            "tool_call_id": "call_a",
            "content": "a.py 的真实内容",
            "is_error": False,
        })

        # 当前停在 result_a，batch 已经不是最新位置。
        before = path.read_bytes()

        try:
            store.mark_pending_tools_unknown(
                expected_leaf_id=batch.id,
            )
        except ValueError as exc:
            assert "位置已变化" in str(exc)
        else:
            raise AssertionError("过时的位置不应允许恢复")

        assert path.read_bytes() == before
        print("拒绝根据过时的位置处理会话：通过")

        marked = store.mark_pending_tools_unknown(
            expected_leaf_id=result_a.id,
        )

        assert marked == ["call_b"]
        assert path.read_bytes().startswith(before)

        branch = store.get_current_branch()
        assert pending_tool_calls(branch) == set()

        # A 的真实结果保持不变。
        assert branch[-2].payload == result_a.payload

        recovery = branch[-1].payload
        assert recovery["tool_call_id"] == "call_b"
        assert recovery["is_error"] is True
        assert recovery["exit_code"] is None

        detail = json.loads(recovery["content"])
        assert detail["source"] == "session_recovery"
        assert detail["status"] == "execution_unknown"

        print("仅为缺少结果的调用追加未知状态，保留真实结果：通过")

        # 新开一个存储对象，确认记录实际保存了。
        reopened = SessionStore(path)
        messages = build_messages(
            reopened.get_current_branch()
        )

        assert messages[-1] == recovery
        print("重新打开后，历史配对完整且保留恢复说明：通过")

        # 没有待处理调用时，不重复追加说明。
        current = reopened.get_current_branch()[-1]
        before = path.read_bytes()

        assert reopened.mark_pending_tools_unknown(
            expected_leaf_id=current.id,
        ) == []

        assert path.read_bytes() == before
        print("无待处理调用时，不重复写入：通过")


if __name__ == "__main__":
    main()