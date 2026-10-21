import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.contracts import ToolContext
from forgepilot.sessions.files.index import FileContextCandidate
from forgepilot.sessions.files.restore import read_current_file_context


def candidate(path):
    return FileContextCandidate(
        path=path,
        tool_name="read_file",
        call_entry_id="old-call-entry",
        result_entry_id="old-result-entry",
    )


def expect_rejected(action):
    try:
        action()
    except ValueError:
        return
    raise AssertionError("应拒绝读取")


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        context = ToolContext(root)
        target = root / "calculator.py"

        target.write_text("旧内容", encoding="utf-8")

        historical = candidate("calculator.py")

        # 模拟工具交互之后文件被修改。
        current = "当前内容\n" * 100
        target.write_bytes(current.encode("utf-8"))

        restored = read_current_file_context(
            historical,
            context=context,
            authorize_read=lambda path: True,
            max_chars=20,
        )

        assert restored.content == current[:20]
        assert restored.total_chars == len(current)
        assert restored.shown_chars == 20
        assert restored.truncated
        assert restored.sha256 == hashlib.sha256(
            current.encode("utf-8")
        ).hexdigest()
        assert restored.source_result_entry_id == "old-result-entry"
        print("读取当前内容，哈希对应完整文件，展示范围明确：通过")

        expect_rejected(lambda: read_current_file_context(
            historical,
            context=context,
            authorize_read=lambda path: False,
        ))
        print("当前权限拒绝时不恢复文件：通过")

        (root / ".env").write_text("测试数据", encoding="utf-8")

        for path in (".env", "../outside.py"):
            expect_rejected(lambda path=path: read_current_file_context(
                candidate(path),
                context=context,
                authorize_read=lambda target: True,
            ))
        print("拒绝敏感路径和工作区外路径：通过")

        (root / "binary.py").write_bytes(b"abc\x00def")
        (root / "invalid.py").write_bytes(b"\xff")

        for path in ("binary.py", "invalid.py"):
            expect_rejected(lambda path=path: read_current_file_context(
                candidate(path),
                context=context,
                authorize_read=lambda target: True,
            ))
        print("拒绝二进制和无效 UTF-8 内容：通过")

        expect_rejected(lambda: read_current_file_context(
            historical,
            context=context,
            authorize_read=lambda path: True,
            max_file_bytes=128,
        ))
        print("文件大小超过上限时停止：通过")


if __name__ == "__main__":
    main()