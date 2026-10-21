from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.contracts import ToolContext
from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.edit import build_edit_tools
from forgepilot.tools.files import read_file

def main():
    registry = ToolRegistry(build_edit_tools())
    # 本脚本只编辑下方临时目录中的测试文件。
    registry.configure_permissions(mode="accept_edits")

    with TemporaryDirectory(prefix="forgepilot-edit-") as directory:
        workspace = Path(directory)
        context = ToolContext(workspace=workspace)
        target = workspace / "calculator.py"

        original = "def add(a, b):\n    return a - b\n"
        target.write_bytes(original.encode("utf-8"))

        result = registry.execute(
            "edit_file",
            {
                "path": "calculator.py",
                "old_text": "return a - b",
                "new_text": "return a + b",
            },
            context=context,
        )

        assert result.ok, result.output
        expected = "def add(a, b):\n    return a + b\n"
        assert target.read_bytes() == expected.encode("utf-8")
        print("唯一匹配：通过")

        before = target.read_bytes()

        result = registry.execute(
            "edit_file",
            {
                "path": "calculator.py",
                "old_text": "不存在的内容",
                "new_text": "replacement",
            },
            context=context,
        )

        assert not result.ok
        assert target.read_bytes() == before
        print("未匹配且文件不变：通过")

        target.write_bytes(b"hello\nhello\n")
        before = target.read_bytes()

        result = registry.execute(
            "edit_file",
            {
                "path": "calculator.py",
                "old_text": "hello",
                "new_text": "world",
            },
            context=context,
        )

        assert not result.ok
        assert target.read_bytes() == before
        print("多处匹配且文件不变：通过")
        # 重叠匹配也必须拒绝，而且不能修改文件。
        target.write_bytes(b"aaa")
        before = target.read_bytes()

        result = registry.execute(
            "edit_file",
            {
                "path": "calculator.py",
                "old_text": "aa",
                "new_text": "b",
            },
            context=context,
        )

        assert not result.ok
        assert target.read_bytes() == before
        print("重叠匹配被拒绝且文件不变：通过")

        # 读取 CRLF 文件，再使用读出的原文进行多行替换。
        original_bytes = (
            b"def add(a, b):\r\n"
            b"    return a - b\r\n"
        )
        target.write_bytes(original_bytes)

        output = read_file(
            path="calculator.py",
            context=context,
        )

        # 去掉 read_file 的元信息，只取文件正文。
        old_text = output.split("\n\n", 1)[1]

        assert old_text.encode("utf-8") == original_bytes

        new_text = old_text.replace(
            "return a - b",
            "return a + b",
        )

        result = registry.execute(
            "edit_file",
            {
                "path": "calculator.py",
                "old_text": old_text,
                "new_text": new_text,
            },
            context=context,
        )

        assert result.ok, result.output
        assert target.read_bytes() == (
            b"def add(a, b):\r\n"
            b"    return a + b\r\n"
        )
        print("CRLF 多行读取与编辑一致：通过")


if __name__ == "__main__":
    main()
