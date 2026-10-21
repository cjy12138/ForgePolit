from forgepilot.paths import PROJECT_ROOT
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from forgepilot.exceptions import ModelError
from forgepilot.models.deepseek import DeepSeekModel
from forgepilot.task_interpreter import TaskInterpreter
from forgepilot.tasks import TaskRequest, route_task


def main():
    project_root = PROJECT_ROOT

    load_dotenv(
        project_root / ".env",
        override=True,
        encoding="utf-8-sig",
    )

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY")

    request = TaskRequest(
        text="把那个问题修一下",
        mode="bugfix",
    )

    with OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com",
        timeout=60.0,
        max_retries=0,
    ) as client:
        model = DeepSeekModel(client=client)
        interpreter = TaskInterpreter(model=model)

        try:
            intent = interpreter.interpret(request)
        except ModelError as exc:
            print("任务理解失败：", exc.code, str(exc))
            return

        decision = route_task(request, intent)

        print("模型理解：")
        print(intent.model_dump_json(indent=2))

        print("程序路由：")
        print(decision.model_dump_json(indent=2))


if __name__ == "__main__":
    main()