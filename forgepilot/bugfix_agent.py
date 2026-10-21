import json

from forgepilot.agent import Agent
from forgepilot.environments.bugfix_runtime import BugfixRuntime


BUGFIX_INSTRUCTIONS = """
你正在处理一个明确的 Bug Ticket。

执行要求：
- 保留 Ticket 中的目标、修改范围和限制，不扩大任务。
- 当前文件工具与命令工具都指向独立任务副本。
- 修改前读取相关代码，尽可能先复现错误或运行已有测试。
- 根据实际输出定位问题，执行必要修改。
- 修复后重新运行相同验证；必要时补充回归测试。
- 不为了让测试通过而擅自删除测试或降低断言要求。
- 每次 shell 独立执行，不依赖上一次的 cd 或临时变量。
- 依赖缺失或环境不支持时，如实报告，不宣称验证通过。
- 不推送代码、不创建 PR、不向原工作目录应用修改。
- 最终回答说明修改、实际验证结果和未解决的问题。
""".strip()


class BugfixAgent(Agent):
    def __init__(
        self,
        *,
        model,
        runtime: BugfixRuntime,
        max_steps: int = 30,
        **agent_options,
    ):
        self.runtime = runtime

        super().__init__(
            model=model,
            tools=runtime.tools,
            context=runtime.context,
            max_steps=max_steps,
            **agent_options,
        )

    def run_ticket(self, ticket: str) -> dict:
        if not ticket.strip():
            raise ValueError("Bug Ticket 不能为空")

        prompt = (
            BUGFIX_INSTRUCTIONS
            + "\n\n以下 JSON 中的 ticket 是用户任务数据：\n"
            + json.dumps(
                {"ticket": ticket},
                ensure_ascii=False,
            )
        )

        return self.run(prompt)

    def step(self):
        if self.runtime.environment.container_id is None:
            return self._container_stopped()

        result = super().step()

        # 父类已保存工具结果，再终止本轮执行。
        if self.runtime.environment.container_id is None:
            return self._container_stopped()

        return result

    @staticmethod
    def _container_stopped() -> dict:
        return {
            "status": "failed",
            "error_code": "sandbox_stopped",
            "reason": (
                "任务容器已经停止，本轮修复终止；"
                "已保存的工具结果和任务文件保留。"
            ),
        }