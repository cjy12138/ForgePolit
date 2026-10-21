from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.artifacts.store import ArtifactStore
from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.snip.transform import snip_tool_message
from forgepilot.sessions.snip.records import hash_tool_payload
from forgepilot.sessions.summarization.artifacts import (
    collect_summary_artifacts,
)


def main():
    with TemporaryDirectory() as directory:
        artifacts = ArtifactStore(Path(directory) / "artifacts")

        content = "完整工具输出\n" * 4000
        ref = artifacts.save_text(content)

        entry = MessageEntry(
            session_id="demo",
            payload={
                "role": "tool",
                "tool_call_id": "call_a",
                "tool_name": "run_python",
                "content": content,
                "is_error": False,
                "exit_code": 0,
                "artifact": ref.model_dump(),
            },
        )

        before = entry.model_dump()

        full_view = {
            "id": entry.id,
            "message": deepcopy(entry.payload),
        }

        sources = collect_summary_artifacts(
            [entry],
            [full_view],
        )

        assert len(sources) == 1
        assert sources[0].has_partial_view is False
        assert sources[0].artifact == ref
        print("完整视图记录完整产物来源：通过")

        partial_view = {
            "id": entry.id,
            "message": snip_tool_message(
                entry.payload,
                artifacts,
            ),
        }

        assert partial_view["message"]["snipped"] is True

        sources = collect_summary_artifacts(
            [entry],
            [partial_view],
        )

        assert sources[0].has_partial_view is True
        assert sources[0].source_sha256 == hash_tool_payload(
            entry.payload
        )
        assert entry.model_dump() == before
        print("片段视图使用原始来源哈希，不修改历史：通过")

        wrong_id = deepcopy(partial_view)
        wrong_id["id"] = "other"

        wrong_status = deepcopy(partial_view)
        wrong_status["message"]["exit_code"] = 1

        wrong_content = deepcopy(full_view)
        wrong_content["message"]["content"] = "其他正文"

        for bad_view in (wrong_id, wrong_status, wrong_content):
            try:
                collect_summary_artifacts([entry], [bad_view])
            except ValueError:
                pass
            else:
                raise AssertionError("错误视图被接受")

        print("拒绝来源错配和视图内容冲突：通过")

        try:
            collect_summary_artifacts([entry], [])
        except ValueError:
            pass
        else:
            raise AssertionError("长度不匹配应被拒绝")

        print("拒绝消息与视图数量不匹配：通过")


if __name__ == "__main__":
    main()