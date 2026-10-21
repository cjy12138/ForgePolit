from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.bugfix.check_bugfix_workspace import git
from forgepilot.environments.bugfix_runtime import (
    start_bugfix_runtime,
)
from copy import deepcopy

from forgepilot.bugfix_agent import BugfixAgent

class SequenceModel:
    def __init__(self, responses):
        self.responses = responses
        self.calls = 0

    def generate(self, *, messages, tools):
        if self.calls >= len(self.responses):
            raise AssertionError("Agent 发出了预期之外的模型请求")

        response = deepcopy(self.responses[self.calls])
        self.calls += 1
        return response


def tool_call(call_id, name, arguments):
    return {
        "type": "tool_call",
        "id": call_id,
        "name": name,
        "arguments": arguments,
    }

def main():
    with TemporaryDirectory(prefix="forgepilot-bugfix-") as directory:
        root = Path(directory)
        source = root / "source"
        source.mkdir()

        original_code = "def add(a, b):\n    return a - b\n"

        (source / "calculator.py").write_text(
            original_code,
            encoding="utf-8",
        )

        (source / "test_calculator.py").write_text(
            "import unittest\n"
            "from calculator import add\n\n"
            "class CalculatorTest(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(add(2, 3), 5)\n",
            encoding="utf-8",
        )

        git(source, "init")
        git(source, "add", ".")
        git(
            source,
            "-c", "user.name=Test",
            "-c", "user.email=test@example.invalid",
            "commit", "-m", "initial",
        )

        runtime = start_bugfix_runtime(
            source=source,
            tasks_root=root / "tasks",
        )

        with ExitStack() as lifecycle:
            lifecycle.callback(runtime.close)

            model = SequenceModel([
                tool_call(
                    "call_read",
                    "read_file",
                    {"path": "calculator.py"},
                ),
                tool_call(
                    "call_before",
                    "run_command",
                    {"command": "python -m unittest -v"},
                ),
                tool_call(
                    "call_edit",
                    "edit_file",
                    {
                        "path": "calculator.py",
                        "old_text": "return a - b",
                        "new_text": "return a + b",
                    },
                ),
                tool_call(
                    "call_after",
                    "run_command",
                    {"command": "python -m unittest -v"},
                ),
                {
                    "type": "final",
                    "content": "已修复 add，并重新运行测试。",
                },
            ])

            agent = BugfixAgent(
                model=model,
                runtime=runtime,
                max_steps=8,
            )

            result = agent.run_ticket(
                "修复 calculator.py 的 add。"
                "只修改 calculator.py，先运行 unittest，"
                "修复后重新运行相同测试。"
            )

            assert result["status"] == "completed", result
            assert result["n_calls"] == 5

            observations = {
                message["tool_call_id"]: message
                for message in result["messages"]
                if message["role"] == "tool"
            }

            assert observations["call_before"]["exit_code"] == 1
            assert observations["call_after"]["exit_code"] == 0
            print("Agent 工具循环完成失败测试、修复与复测：通过")

            assert (
                           runtime.task.path / "calculator.py"
                   ).read_text(encoding="utf-8") == (
                       "def add(a, b):\n    return a + b\n"
                   )

            assert (
                           source / "calculator.py"
                   ).read_text(encoding="utf-8") == original_code
            print("Agent 只修改任务副本：通过")

            # 同一个容器里再验证一次超时停止。
            timeout_model = SequenceModel([
                tool_call(
                    "call_timeout",
                    "run_command",
                    {
                        "command": "python -c 'import time; time.sleep(20)'",
                        "timeout_seconds": 1,
                    },
                ),
                {
                    "type": "final",
                    "content": "这条响应不应该被请求。",
                },
            ])

            timeout_agent = BugfixAgent(
                model=timeout_model,
                runtime=runtime,
                max_steps=8,
            )

            timeout_result = timeout_agent.run_ticket(
                "运行指定的耗时命令，验证超时处理。"
            )

            assert timeout_result["status"] == "failed"
            assert timeout_result["error_code"] == "sandbox_stopped"
            assert timeout_model.calls == 1

            timeout_receipts = [
                message
                for message in timeout_result["messages"]
                if message["role"] == "tool"
            ]

            assert len(timeout_receipts) == 1
            assert timeout_receipts[0]["is_error"]
            assert "STATUS: timeout" in timeout_receipts[0]["content"]
            print("超时结果保留，Agent 不再请求模型：通过")
        assert runtime.environment.container_id is None
        assert runtime.task.path.exists()
        print("关闭容器后，任务副本仍保留：通过")


if __name__ == "__main__":
    main()
