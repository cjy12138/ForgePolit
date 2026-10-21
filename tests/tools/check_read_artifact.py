import json
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.artifacts.store import ArtifactStore
from forgepilot.contracts import ToolContext
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.artifacts import build_artifact_tools


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        context = ToolContext(root)

        store = SessionStore(root / "session.jsonl")
        store.create(SessionHeader(
            project_id="demo",
            owner_id="local",
            cwd=directory,
        ))

        user = store.append_message({
            "role": "user",
            "content": "生成输出。",
        })

        store.append_message({
            "role": "assistant",
            "type": "tool_call",
            "id": "call_output",
            "name": "run_python",
            "arguments": {"args": ["demo.py"]},
        })

        artifacts = ArtifactStore(root / "artifacts")
        text = "甲乙丙丁戊"
        ref = artifacts.save_text(text)

        result_entry = store.append_message({
            "role": "tool",
            "tool_call_id": "call_output",
            "content": text,
            "is_error": False,
            "exit_code": 0,
            "artifact": ref.model_dump(),
        })

        registry = ToolRegistry(build_artifact_tools(
            session_store=store,
            artifact_store=artifacts,
        ))

        def read(**arguments):
            return registry.execute(
                "read_artifact",
                arguments,
                context=context,
            )

        first = read(
            artifact_id=ref.id,
            offset=0,
            limit=2,
        )
        assert first.ok
        page = json.loads(first.output)
        assert page["content"] == "甲乙"
        assert page["next_offset"] == 2

        second = read(
            artifact_id=ref.id,
            offset=page["next_offset"],
            limit=3,
        )
        assert second.ok
        page = json.loads(second.output)
        assert page["content"] == "丙丁戊"
        assert page["next_offset"] is None
        print("按字符分页，返回正确的继续位置：通过")

        # 文件存在，但没有被当前分支引用。
        unreferenced = artifacts.save_text("不能直接读取")
        assert not read(artifact_id=unreferenced.id).ok
        print("拒绝未被当前分支引用的产物：通过")

        # 切回工具调用之前，原结果不在当前分支中。
        store.move_cursor(user.id)
        assert not read(artifact_id=ref.id).ok

        store.move_cursor(result_entry.id)
        assert read(artifact_id=ref.id).ok
        print("切换分支后重新判断访问范围：通过")

        assert not read(artifact_id="../outside").ok
        assert not read(artifact_id=ref.id, offset=-1).ok
        assert not read(artifact_id=ref.id, offset=6).ok
        assert not read(artifact_id=ref.id, limit=8001).ok

        # 模型不能通过参数提交自己的哈希。
        assert not read(
            artifact_id=ref.id,
            sha256="0" * 64,
        ).ok
        print("拒绝非法范围、路径和额外参数：通过")

        path = artifacts.root / f"{ref.id}.txt"
        path.write_bytes(b"changed")

        assert not read(artifact_id=ref.id).ok
        print("产物损坏时拒绝返回内容：通过")


if __name__ == "__main__":
    main()