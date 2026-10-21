import json

from forgepilot.models.deepseek import DeepSeekModel





if __name__ == "__main__":
    from forgepilot.tool_registry import ToolRegistry
    from forgepilot.tools.files import build_file_tools

    model = DeepSeekModel(client=None)

    messages = [
        {
            "role": "user",
            "content": "读取 README.md。",
        },
        {
            "role": "assistant",
            "type": "tool_call",
            "id": "call_1",
            "name": "read_file",
            "arguments": {"path": "README.md"},
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "这是 ForgePilot 项目。",
            "is_error": False,
        },
    ]

    converted = model._convert_messages(messages)

    print(json.dumps(converted, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            model._convert_tools(ToolRegistry(build_file_tools()).definitions()),
            ensure_ascii=False,
            indent=2,
        )
    )

    arguments = converted[1]["tool_calls"][0]["function"]["arguments"]

    assert isinstance(arguments, str)
    assert json.loads(arguments) == {"path": "README.md"}
    assert converted[2]["tool_call_id"] == "call_1"
    assert json.loads(converted[2]["content"])["ok"] is True
