import json

from forgepilot.sessions.models import MessageEntry
from forgepilot.tasks import TaskMode, TaskRequest


def run_task_turn(
    harness,
    text: str,
    *,
    mode: TaskMode | None = None,
) -> dict | None:
    original_request = TaskRequest(text=text, mode=mode)
    request = original_request
    clarifications = []

    while True:
        result = harness.run(request)

        if result["status"] != "needs_input":
            return result

        print("需要补充信息：", result["question"])

        if len(clarifications) >= 3:
            print("已达到本轮澄清上限，尚未启动执行。")
            return None

        try:
            while True:
                answer = input(
                    "请补充信息，输入 /cancel 取消本轮："
                ).strip()

                if answer:
                    break

                print("回答不能为空。")

        except (EOFError, KeyboardInterrupt):
            print("\n已取消本轮。")
            return None

        if answer == "/cancel":
            print("已取消本轮。")
            return None

        clarifications.append({
            "question": result["question"],
            "answer": answer,
        })

        request = TaskRequest(
            text=(
                "请结合原始需求和后续澄清问答理解任务。\n"
                + json.dumps(
                    {
                        "original_request": original_request.text,
                        "clarifications": clarifications,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            ),
            mode=original_request.mode,
        )

def recent_dialogue(sessions) -> list[dict]:
    dialogue = []

    for entry in sessions.get_current_branch():
        if not isinstance(entry, MessageEntry):
            continue

        message = entry.payload

        is_user = message.get("role") == "user"
        is_final = (
            message.get("role") == "assistant"
            and message.get("type") == "final"
        )

        if not (is_user or is_final):
            continue

        content = message.get("content")

        if not isinstance(content, str):
            continue

        dialogue.append({
            "role": message["role"],
            "content": content,
        })

    recent = dialogue[-6:]

    for message in recent:
        if len(message["content"]) > 1500:
            message["content"] = (
                message["content"][:1500]
                + "\n[本条历史已截断]"
            )

    return recent
