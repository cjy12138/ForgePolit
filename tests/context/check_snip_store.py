from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.artifacts.store import ArtifactStore
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.snip.records import (
    SnipRecord,
    hash_tool_payload,
)
from forgepilot.sessions.store import SessionStore
from forgepilot.sessions.snip.request_view import build_recorded_snip_request
from forgepilot.sessions.snip.transform import snip_tool_message
from unittest.mock import patch

from forgepilot.sessions.compaction.compactor import SessionCompactor
from forgepilot.sessions.summarization.summarizer import ContextSummarizer

from tests.context.check_session_compactor import FakeSummaryModel
def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "session.jsonl"

        store = SessionStore(path)
        header = SessionHeader(
            project_id="demo",
            owner_id="local",
            cwd=directory,
        )
        store.create(header)

        user = store.append_message({
            "role": "user",
            "content": "读取项目说明",
        })

        store.append_message({
            "role": "assistant",
            "type": "tool_call",
            "id": "call_a",
            "name": "read_file",
            "arguments": {"path": "README.md"},
        })

        content = "x" * 10000
        artifacts = ArtifactStore(root / "artifacts")
        ref = artifacts.save_text(content)

        result = store.append_message({
            "role": "tool",
            "tool_call_id": "call_a",
            "content": content,
            "is_error": False,
            "exit_code": 0,
            "tool_name": "read_file",
            "artifact": ref.model_dump(),
        })

        record = SnipRecord(
            session_id=header.session_id,
            source_leaf_id=result.id,
            target_entry_id=result.id,
            source_sha256=hash_tool_payload(result.payload),
        )

        before = store.replay()
        assert store.append_snip(record) is True
        assert store.replay() == before
        print("写入不改变消息树和游标：通过")

        saved = path.read_bytes()

        duplicate = SnipRecord(
            session_id=header.session_id,
            source_leaf_id=result.id,
            target_entry_id=result.id,
            source_sha256=record.source_sha256,
        )

        assert store.append_snip(duplicate) is False
        assert path.read_bytes() == saved
        print("重复发布不重复写入：通过")

        reopened = SessionStore(path)
        assert reopened.get_current_snip_records() == [record]
        print("重新打开后恢复记录：通过")

        reopened.move_cursor(user.id)
        assert reopened.get_current_snip_records() == []

        # 在另一条分支继续，不能混入旧分支的 Snip。
        reopened.append_message({
            "role": "user",
            "content": "换一个任务",
        })
        assert reopened.get_current_snip_records() == []

        reopened.move_cursor(result.id)
        assert reopened.get_current_snip_records() == [record]
        print("分支切换隔离记录：通过")

        reopened.append_message({
            "role": "user",
            "content": "继续",
        })
        assert reopened.get_current_snip_records() == [record]

        before_rejected = path.read_bytes()

        try:
            reopened.append_snip(record)
        except ValueError as exc:
            assert "位置已变化" in str(exc)
        else:
            raise AssertionError("旧锚点记录不应再次发布")

        assert path.read_bytes() == before_rejected
        print("已有记录继续适用，拒绝在旧位置发布：通过")
        # 原来的工具结果需要变成较早历史，
        # 才不会被“最近两组工具交互”保护。
        for index in range(2):
            call_id = f"later_{index}"

            reopened.append_message({
                "role": "assistant",
                "type": "tool_call",
                "id": call_id,
                "name": "read_file",
                "arguments": {"path": "README.md"},
            })

            reopened.append_message({
                "role": "tool",
                "tool_call_id": call_id,
                "content": "近期输出",
                "is_error": False,
                "exit_code": 0,
            })

        current_leaf = reopened.replay()[2]
        reopened = SessionStore(path)

        saved = path.read_bytes()

        view = build_recorded_snip_request(
            reopened.get_current_branch(),
            artifacts,
            reopened.get_current_snip_records(),
        )

        restored_tool = next(
            message
            for message in view
            if message.get("role") == "tool"
            and message.get("tool_call_id") == "call_a"
        )

        expected = snip_tool_message(
            result.payload,
            artifacts,
            head_chars=record.head_chars,
            tail_chars=record.tail_chars,
        )

        assert restored_tool == expected
        assert restored_tool["snipped"] is True
        assert path.read_bytes() == saved

        raw_result = next(
            node
            for node in reopened.get_current_branch()
            if node.id == result.id
        )
        assert raw_result.payload["content"] == content
        assert "snipped" not in raw_result.payload

        print("重新打开后恢复截断视图，原文和文件不变：通过")

        # 切回早期位置，该结果重新成为近期工具结果。
        reopened.move_cursor(result.id)

        early_view = build_recorded_snip_request(
            reopened.get_current_branch(),
            artifacts,
            reopened.get_current_snip_records(),
        )

        early_tool = next(
            message
            for message in early_view
            if message.get("role") == "tool"
        )

        assert early_tool["content"] == content
        assert "snipped" not in early_tool
        print("恢复记录仍遵守近期工具组保护：通过")

        reopened.move_cursor(current_leaf)

        summary_model = FakeSummaryModel()

        compactor = SessionCompactor(
            reopened,
            ContextSummarizer(summary_model),
        )

        compaction = compactor.compact(
            keep_recent_groups=1,
        )

        assert compaction is not None
        assert summary_model.calls == 1

        # 摘要发布后，重新打开会话。
        reopened = SessionStore(path)
        branch = reopened.get_current_branch()
        records = reopened.get_current_snip_records()

        # 控制记录仍保存着，但目标已经不在有效请求正文中。
        assert records == [record]

        saved = path.read_bytes()

        with patch.object(
            artifacts,
            "read_text",
            wraps=artifacts.read_text,
        ) as read_text:
            compacted_view = build_recorded_snip_request(
                branch,
                artifacts,
                records,
            )

            # 被摘要覆盖的旧结果，不再读取产物来生成 Snip。
            read_text.assert_not_called()

        assert not any(
            message.get("role") == "tool"
            and message.get("tool_call_id") == "call_a"
            for message in compacted_view
        )

        assert any(
            isinstance(message.get("content"), str)
            and message["content"].startswith(
                "以下是历史对话的压缩摘要"
            )
            for message in compacted_view
        )

        # 最近的一整组工具调用与结果仍然保留。
        assert any(
            message.get("type") == "tool_call"
            and message.get("id") == "later_1"
            for message in compacted_view
        )
        assert any(
            message.get("role") == "tool"
            and message.get("tool_call_id") == "later_1"
            for message in compacted_view
        )

        assert path.read_bytes() == saved
        print("摘要覆盖旧结果后，不重复发送 Snip：通过")

        raw_result = next(
            node
            for node in branch
            if node.id == result.id
        )

        assert raw_result.payload["content"] == content
        assert artifacts.read_text(ref) == content
        print("摘要后仍保留完整历史和产物：通过")

        # 切回摘要前的末尾，原来的 Snip 视图应能恢复。
        reopened.move_cursor(current_leaf)

        previous_view = build_recorded_snip_request(
            reopened.get_current_branch(),
            artifacts,
            reopened.get_current_snip_records(),
        )

        assert previous_view == view
        print("切回摘要前历史，恢复原来的 Snip 视图：通过")

        reopened.move_cursor(compaction.id)


if __name__ == "__main__":
    main()