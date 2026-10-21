def read_task() -> str | None:
    while True:
        try:
            text = input("\n你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出。")
            return None

        if text == "/exit":
            return None

        if not text:
            print("请输入任务，或输入 /exit 退出。")
            continue

        return text