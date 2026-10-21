"""离线验证批量工具调用，不调用 API，不运行真实命令。"""
from pathlib import Path
from types import SimpleNamespace as NS
import json
import unittest

from pydantic import BaseModel

from forgepilot.agent import Agent
from forgepilot.contracts import ToolContext, response_adapter
from forgepilot.exceptions import ModelError
from forgepilot.models.deepseek import DeepSeekModel
from forgepilot.task_interpreter import TaskInterpreter
from forgepilot.tasks import TaskRequest
from forgepilot.tool_registry import ToolRegistry


def call(identifier, name="probe", arguments=None):
    return {
        "type": "tool_call", "id": identifier, "name": name,
        "arguments": {"value": identifier} if arguments is None else arguments,
    }


class Input(BaseModel):
    value: str


class BatchChecks(unittest.TestCase):
    def make_agent(self, calls):
        seen = []

        def handler(*, value, context):
            seen.append(value)
            return value

        class Model:
            def generate(self, messages, tools):
                if len(messages) == 1:
                    return {"type": "tool_batch", "calls": calls}
                return {"type": "final", "content": "done"}

        registry = ToolRegistry({"probe": {
            "name": "probe", "description": "test",
            "input_model": Input, "handler": handler,
        }})
        # 此脚本验证内存中的假工具批次，不依赖交互式权限输入。
        registry.configure_permissions(mode="bypass")
        return Agent(Model(), registry, ToolContext(Path.cwd())), seen

    def test_order_and_wire_format(self):
        agent, seen = self.make_agent([call("a"), call("b")])
        result = agent.run("test")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(seen, ["a", "b"])
        wire = DeepSeekModel(None)._convert_messages(result["messages"])
        self.assertEqual([c["id"] for c in wire[1]["tool_calls"]], ["a", "b"])
        self.assertEqual([m["tool_call_id"] for m in wire[2:4]], ["a", "b"])

    def test_tool_error_still_has_matching_result(self):
        agent, seen = self.make_agent([call("a", "missing"), call("b")])
        result = agent.run("test")
        self.assertTrue(result["messages"][2]["is_error"])
        self.assertFalse(result["messages"][3]["is_error"])
        self.assertEqual(seen, ["b"])

    def test_invalid_batch_executes_nothing(self):
        for calls in ([call("a"), call("a")], [call("a"), {"type": "tool_call"}]):
            agent, seen = self.make_agent(calls)
            self.assertEqual(agent.run("test")["status"], "failed")
            self.assertEqual(seen, [])

    def test_adapter_keeps_all_calls_and_single_compatibility(self):
        for count in (1, 2):
            sdk_calls = [NS(id=str(i), function=NS(
                name="probe", arguments=json.dumps({"value": str(i)}),
            )) for i in range(count)]
            response = NS(choices=[NS(finish_reason="tool_calls", message=NS(
                tool_calls=sdk_calls,
            ))])
            client = NS(chat=NS(completions=NS(create=lambda **kwargs: response)))
            raw = DeepSeekModel(client).generate([], [])
            response_adapter.validate_python(raw)
            self.assertEqual(raw["type"], "tool_call" if count == 1 else "tool_batch")
            if count == 2:
                self.assertEqual(len(raw["calls"]), 2)

    def test_interpreter_rejects_multiple_submissions(self):
        class Model:
            count = 0

            def generate(self, messages, tools):
                self.count += 1
                return {"type": "tool_batch", "calls": [call("a"), call("b")]}

        model = Model()
        with self.assertRaises(ModelError) as error:
            TaskInterpreter(model).interpret(TaskRequest(text="test"))
        self.assertEqual(error.exception.code, "invalid_task_intent")
        self.assertEqual(model.count, 2)


if __name__ == "__main__":
    unittest.main()
