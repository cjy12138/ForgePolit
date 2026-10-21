from forgepilot.tool_registry import ToolRegistry
from forgepilot.tools.files import build_file_tools
from forgepilot.tools.search import build_search_tools


def main():
    registry = ToolRegistry(
        build_file_tools(),
        build_search_tools(),
    )

    names = {
        tool["name"]
        for tool in registry.definitions()
    }

    assert names == {"read_file", "list_files", "grep_files"}
    print("多个工具组注册：通过")

    try:
        ToolRegistry(
            build_file_tools(),
            build_file_tools(),
        )
    except ValueError as exc:
        assert "工具重复注册" in str(exc)
        print("拒绝同名工具：通过")
    else:
        raise AssertionError("重复工具不应该注册成功")

    broken = build_file_tools()
    broken["read_file"]["handler"] = None

    try:
        ToolRegistry(broken)
    except TypeError as exc:
        assert "handler" in str(exc)
        print("拒绝不可调用的 handler：通过")
    else:
        raise AssertionError("非法 handler 不应该注册成功")


if __name__ == "__main__":
    main()