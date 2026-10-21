from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.memory.dream_state import AutoDreamStateStore
from forgepilot.memory.store import MarkdownMemoryStore


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        state = AutoDreamStateStore(MarkdownMemoryStore(root))

        start = 1_000_000.0
        one_day = 24 * 60 * 60

        # 首次检查建立基线，不能立即触发。
        assert state.prepare_if_due(now=start) == {
            "status": "too_soon"
        }
        print("首次检查建立时间基线：通过")

        # 同一会话完成五轮，仍只算一个会话。
        for index in range(5):
            state.record_activity(
                "session_a",
                f"final_{index}",
                now=start + index,
            )

        outcome = state.prepare_if_due(now=start + one_day)

        assert outcome == {
            "status": "not_enough_sessions",
            "sessions": 1,
        }
        print("同一会话多轮活动只计一次：通过")

        # 再补充四个不同会话。
        for index in range(4):
            state.record_activity(
                f"session_other_{index}",
                "final_1",
                now=start + 10,
            )

        # 会话数量够了，但时间还差一秒。
        assert state.prepare_if_due(
            now=start + one_day - 1
        ) == {"status": "too_soon"}
        print("会话数量满足，但不足24小时不触发：通过")

        # 模拟程序重启，从磁盘重新读取状态。
        reopened = AutoDreamStateStore(
            MarkdownMemoryStore(root)
        )

        outcome = reopened.prepare_if_due(
            now=start + one_day
        )

        assert outcome["status"] == "ready"
        assert outcome["new_sessions"] == 5
        assert outcome["activity"]["session_a"] == "final_4"
        print("重启后保留状态，两个条件满足时准备整理：通过")

        # 准备整理不能提前标记为成功。
        before = reopened.path.read_text(encoding="utf-8")

        again = reopened.prepare_if_due(
            now=start + one_day + 1
        )

        after = reopened.path.read_text(encoding="utf-8")

        assert again["status"] == "ready"
        assert before == after
        print("准备整理不更新成功时间和已处理水位：通过")


if __name__ == "__main__":
    main()