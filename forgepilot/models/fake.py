from forgepilot.exceptions import ModelError

class InvalidModel:
    def generate(self, messages, tools):
        return {
            "type": "tool_call",
            "name": "read_file",
            "arguments": {"path": "README.md"},
            # 故意缺少 id
        }

class FakeModel:
    def generate(self, messages, tools):
        tool_result = [
            message
            for message in messages
            if message.get('role') == 'tool'
        ]

        if not tool_result:
            return {
                "type": "tool_call",
                "id": "call_1",
                "name": "read_file",
                "arguments": {"path": "README.md"},
            }

        latest_result = tool_result[-1]

        print("模型收到的工具名称：", [tool["name"] for tool in tools])
        return {
            "type": "final",
            "content": (
                "已收到文件内容，前 120 个字符如下：\n"
                + latest_result["content"][:120]
            ),
        }

class TimeoutModel:
    def generate(self, messages, tools):
        raise ModelError("timeout", "模拟模型请求超时。")
