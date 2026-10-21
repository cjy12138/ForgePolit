import json

from forgepilot.exceptions import ModelError
from forgepilot.tasks import TaskRequest, route_task

class Harness:
    def __init__(
            self,
            interpreter,
            agent,
            history_provider=None,
            bugfix_handler=None,
            coding_prepare=None,
    ):
        self.interpreter = interpreter
        self.agent = agent
        self.history_provider = history_provider
        self.bugfix_handler = bugfix_handler
        self.coding_prepare = coding_prepare

    def run(self,request: TaskRequest):
        try:
            if self.history_provider is None:
                intent = self.interpreter.interpret(request)
            else:
                intent = self.interpreter.interpret(
                    request,
                    history=self.history_provider(),
                )
        except ModelError as exc:
            return {
                "status": "failed",
                "stage": "interpretation",
                "error_code": exc.code,
                "reason": str(exc)
            }

        decision = route_task(request,intent)

        if decision.status == "needs_input":
            return {
                "status": "needs_input",
                "question": decision.question,
                "mode": decision.mode,
                "goal": decision.goal,
            }

        task_data = {
            "original_request": request.text,
            "interpreted_goal": decision.goal,
            "selected_mode": decision.mode,
        }
        if decision.mode == "bugfix":
            if self.bugfix_handler is None:
                result = {
                    "status": "failed",
                    "reason": "当前入口未配置 bugfix 执行服务。",
                    "messages": [],
                    "n_calls": 0,
                }
            else:
                try:
                    result = self.bugfix_handler(task_data)
                except (ModelError, ValueError, RuntimeError, OSError) as exc:
                    result = {
                        "status": "failed",
                        "reason": str(exc),
                        "messages": [],
                        "n_calls": 0,
                    }

            return {
                **result,
                "stage": "execution",
                "mode": decision.mode,
                "goal": decision.goal,
            }

        prompt = (
                "请处理下面的编程任务。\n"
                "原始需求中的要求和限制必须保留，理解后的目标仅作辅助。\n"
                "修改文件前先读取相关内容。"
                "适用时运行已有测试，根据实际结果报告，不虚构验证。\n"
                "每次响应只调用一个工具。\n"
                "当前运行于本地教学环境，模式标签不代表已启用容器隔离。\n\n"
                "任务数据：\n"
                + json.dumps(task_data, ensure_ascii=False, indent=2)
        )

        try:
            if self.coding_prepare is not None:
                self.coding_prepare(task_data)

            result = self.agent.run(prompt)

        except (ModelError, ValueError, RuntimeError, OSError) as exc:
            result = {
                "status": "failed",
                "reason": str(exc),
                "messages": [],
                "n_calls": 0,
            }

        return {
            **result,
            "stage": "execution",
            "mode": decision.mode,
            "goal": decision.goal,
        }



