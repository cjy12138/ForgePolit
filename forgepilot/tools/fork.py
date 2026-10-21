from collections.abc import Callable
from functools import partial

from forgepilot.contracts import ToolContext, ToolResult
from forgepilot.fork.models import ForkTasksInput
from forgepilot.permissions.models import ToolCheckResult
from forgepilot.permissions.tool_checks import is_plan_fork


def check_fork_permissions(request):
    if is_plan_fork(request):
        return ToolCheckResult(action="allow", reason="plan 模式派发的子任务仍限定为 plan。")
    return ToolCheckResult(action="abstain", reason="派发子任务需继续检查当前权限模式。")


def fork_tasks(
    *,
    context: ToolContext,
    dispatch: Callable[[ForkTasksInput, ToolContext], ToolResult],
    tasks: list[dict],
) -> ToolResult:
    params = ForkTasksInput.model_validate({"tasks": tasks})
    result = dispatch(params, context)
    if not isinstance(result, ToolResult):
        raise TypeError("Fork 派发服务必须返回 ToolResult")
    return result


def build_fork_tools(
    *,
    dispatch: Callable[[ForkTasksInput, ToolContext], ToolResult],
    agent_descriptions: str = "",
):
    """派发服务负责快照、权限继承、执行和恢复；工具只处理入口。"""
    if not callable(dispatch):
        raise TypeError("Fork 工具必须配置派发服务")

    return {
        "fork_tasks": {
            "name": "fork_tasks",
            "description": (
                "向独立上下文的子 Agent 派发一个任务，并接收执行结果。"
                "适用于可独立探索的代码问题；子任务之间不通信。"
                "通过 base_tools 指定基础工具名称，只能使用允许的已注册工具。"
                "也可用 agent_type 选择 Markdown 定义的工具配置，用 skills 指定启动预加载的 Skill。"
                "子任务权限模式可指定，省略时继承主任务；不能绕过安全策略。"
                "最终答复需结合子任务状态，不把失败或中断当作完成。"
                + ("\n可用子 Agent 定义：\n" + agent_descriptions if agent_descriptions else "")
            ),
            "input_model": ForkTasksInput,
            "check_permissions": check_fork_permissions,
            "handler": partial(
                fork_tasks,
                dispatch=dispatch,
            ),
        }
    }
