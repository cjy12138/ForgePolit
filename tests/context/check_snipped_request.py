from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.artifacts.store import ArtifactStore
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.snip.request_view import build_snipped_request
from forgepilot.sessions.store import SessionStore


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

        store.append_message({
            "role": "user",
            "content": "检查项目，不修改测试文件。",
        })

        originals = {}

        for index in range(5):
            call_id = f"call_{index}"
            text = f"第 {index} 组\n" + "输出内容\n" * 4000
            originals[call_id] = text

            store.append_message({
                "role": "assistant",
                "type": "tool_call",
                "id": call_id,
                "name": "run_python",
                "arguments": {"args": ["demo.py"]},
            })

            ref = artifacts.save_text(text)
            store.append_message({
                "role": "tool",
                "tool_call_id": call_id,
                "tool_name": "run_python",
                "content": text,
                "is_error": False,
                # 第二组模拟命令执行完毕，但退出码非零。
                "exit_code": 1 if index == 1 else 0,
                "artifact": ref.model_dump(),
            })

        branch = store.get_current_branch()
        before_nodes = [node.model_dump() for node in branch]
        before_file = store.path.read_bytes()

        view = build_snipped_request(
            branch,
            artifacts,
            keep_recent_tool_groups=2,
        )

        results = {
            message["tool_call_id"]: message
            for message in view
            if message["role"] == "tool"
        }

        assert results["call_0"]["snipped"] is True
        assert results["call_2"]["snipped"] is True
        print("较早的成功输出可以缩减：通过")

        for call_id in ("call_3", "call_4"):
            assert results[call_id]["content"] == originals[call_id]
            assert not results[call_id].get("snipped", False)
        print("最近两组工具交互保留原文：通过")

        assert results["call_1"]["content"] == originals["call_1"]
        assert results["call_1"]["exit_code"] == 1
        print("较早的非零退出码结果仍保留原文：通过")

        assert view[0]["content"] == "检查项目，不修改测试文件。"
        assert len(view) == len(branch)
        assert [node.model_dump() for node in branch] == before_nodes
        assert store.path.read_bytes() == before_file
        print("消息数量、原始要求和磁盘历史不变：通过")


if __name__ == "__main__":
    main()