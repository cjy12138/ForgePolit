from forgepilot.paths import PROJECT_ROOT
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


def main():
    # 本地开发以项目 .env 为准，覆盖当前进程中可能遗留的旧配置。
    load_dotenv(
        PROJECT_ROOT / ".env",
        override=True,
        encoding="utf-8-sig",
    )
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("请先设置 DEEPSEEK_API_KEY 环境变量")

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com",
        timeout=60.0,
        max_retries=0,
    )

    response = client.chat.completions.create(
        model="deepseek-v4-pro",
        messages=[
            {
                "role": "user",
                "content": "请只回复：连接成功",
            }
        ],
        max_tokens=64,
        stream=False,
        extra_body={
            "thinking": {"type": "disabled"},
        },
    )

    print("结束原因：", response.choices[0].finish_reason)
    print("模型回答：", response.choices[0].message.content)

    if response.usage is not None:
        print("Token 用量：", response.usage.model_dump())


if __name__ == "__main__":
    main()
