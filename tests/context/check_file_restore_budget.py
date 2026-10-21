import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.contracts import ToolContext
from forgepilot.sessions.files.budget import add_current_file_context
from forgepilot.sessions.models import MessageEntry


class FileCountModel:
    def estimate_input_tokens(self, messages, tools):
        count = 0

        for message in messages:
            content = message.get("content")

            if not isinstance(content, str):
                continue

            try:
                data = json.loads(content)
            except ValueError:
                continue

            if (
                isinstance(data, dict)
                and data.get("type") == "current_file_context"
            ):
                count += len(data["files"])

        # 假计数：基础请求 100，每个恢复文件 100。
        return 100 + count * 100

    def generate(self, messages, tools):
        raise AssertionError("文件恢复不能请求模型")


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        context = ToolContext(root)
        branch = []

        def add(payload):
            entry = MessageEntry(
                session_id="demo",
                parent_id=branch[-1].id if branch else None,
                payload=payload,
            )
            branch.append(entry)

        add({"role": "user", "content": "检查项目。"})

        for name in ("a.py", "b.py"):
            (root / name).write_text(
                f"{name} 的当前内容",
                encoding="utf-8",
            )
            add({
                "role": "assistant",
                "type": "tool_call",
                "id": name,
                "name": "read_file",
                "arguments": {"path": name},
            })
            add({
                "role": "tool",
                "tool_call_id": name,
                "content": "历史上的旧内容",
                "is_error": False,
            })

        base = [deepcopy(entry.payload) for entry in branch]
        before = deepcopy(base)

        result = add_current_file_context(
            branch,
            base,
            model=FileCountModel(),
            tools=[],
            context=context,
            authorize_read=lambda path: True,
            input_limit_tokens=200,
        )

        # b.py 最近操作，先恢复；a.py 加入后会超过预算。
        assert result.restored_paths == ["b.py"]
        assert any(
            item["path"] == "a.py"
            and item["reason"] == "token_budget"
            for item in result.skipped
        )

        data = json.loads(result.messages[-1]["content"])
        assert data["files"][0]["content"] == "b.py 的当前内容"
        print("近期文件优先，预算不足时跳过，读取当前内容：通过")

        assert base == before
        assert len(result.messages) == len(base) + 1
        print("只增加临时参考消息，不修改基础历史：通过")

        denied = add_current_file_context(
            branch,
            base,
            model=FileCountModel(),
            tools=[],
            context=context,
            authorize_read=lambda path: False,
            input_limit_tokens=400,
        )

        assert denied.restored_paths == []
        assert denied.messages == base
        assert len(denied.skipped) == 2
        print("权限拒绝时不注入文件内容：通过")

        limited = add_current_file_context(
            branch,
            base,
            model=FileCountModel(),
            tools=[],
            context=context,
            authorize_read=lambda path: True,
            input_limit_tokens=400,
            max_total_chars=3,
        )

        data = json.loads(limited.messages[-1]["content"])
        assert sum(
            item["shown_chars"]
            for item in data["files"]
        ) <= 3
        print("恢复正文的总字符数受限：通过")


if __name__ == "__main__":
    main()