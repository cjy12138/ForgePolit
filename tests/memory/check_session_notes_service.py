from pathlib import Path
from tempfile import TemporaryDirectory

from tests.memory.check_session_notes_agent import FakeNotesModel
from forgepilot.memory.notes_agent import SessionNotesAgent
from forgepilot.memory.notes_service import (
    SessionNotesService,
    estimate_history_tokens,
)
from forgepilot.memory.notes_store import SessionNotesStore
from forgepilot.memory.snapshot import build_memory_snapshot
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore


def append_turn(sessions, text):
    sessions.append_message({
        "role": "user",
        "content": text,
    })
    sessions.append_message({
        "role": "assistant",
        "type": "final",
        "content": "已收到，尚未执行新的测试。",
    })


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)

        sessions = SessionStore(root / "session.jsonl")
        sessions.create(SessionHeader(
            owner_id="local-user",
            project_id="demo",
            cwd=str(root),
        ))

        append_turn(sessions, "检查 calculator.py。")

        initial_snapshot = build_memory_snapshot(
            sessions,
            require_final=False,
        )
        initial_tokens = estimate_history_tokens(
            initial_snapshot
        )

        model = FakeNotesModel()
        agent = SessionNotesAgent(
            model=model,
            context_budget=ContextBudget(
                context_window=16000,
                max_output_tokens=1024,
                safety_margin=1000,
            ),
        )
        notes_store = SessionNotesStore(sessions)

        service = SessionNotesService(
            sessions=sessions,
            notes_store=notes_store,
            agent=agent,
            first_at=initial_tokens + 100,
            update_every=500,
        )

        result = service.update_if_due()

        assert result["status"] == "below_threshold"
        assert model.calls == 0
        assert not notes_store.state_path.exists()
        print("首次未达到阈值，不调用模型：通过")

        # 增加足够的原始历史，触发首次生成。
        append_turn(sessions, "补充任务说明。" * 100)

        result = service.update_if_due()

        assert result["status"] == "updated"
        assert model.calls == 1

        first = notes_store.get_current()
        assert first is not None
        assert first.history_tokens == result["history_tokens"]

        # 历史没有变化，重复检查不会重复生成。
        result = service.update_if_due()

        assert result["status"] == "below_threshold"
        assert model.calls == 1
        print("首次生成及重复检查去重：通过")

        # 少量新增历史，尚未达到增量阈值。
        append_turn(sessions, "继续。")

        result = service.update_if_due()

        assert result["status"] == "below_threshold"
        assert model.calls == 1
        assert notes_store.get_current().history_tokens == (
            first.history_tokens
        )
        print("新增历史不足时不更新水位：通过")

        # 再增加历史，触发第二次生成。
        append_turn(sessions, "新增工作记录。" * 200)

        result = service.update_if_due()

        assert result["status"] == "updated"
        assert model.calls == 2

        second = notes_store.get_current()
        assert second is not None
        assert second.history_tokens - first.history_tokens >= 500
        print("达到增量阈值后更新：通过")

        # 已达到阈值，但模型提交非法来源。
        append_turn(sessions, "后续工作记录。" * 200)

        state_before = notes_store.state_path.read_bytes()
        markdown_before = notes_store.markdown_path.read_bytes()

        model.invalid_source = True
        result = service.update_if_due()

        assert result["status"] == "failed"
        assert model.calls == 3
        assert notes_store.state_path.read_bytes() == state_before
        assert notes_store.markdown_path.read_bytes() == markdown_before

        # 不新增历史，纠正模型响应后仍应能重试。
        model.invalid_source = False
        result = service.update_if_due()

        assert result["status"] == "updated"
        assert model.calls == 4
        print("生成失败不推进水位，允许重试：通过")

        # 切换到另一条较短分支。
        sessions.move_cursor(initial_snapshot.user_entry_id)
        sessions.append_message({
            "role": "assistant",
            "type": "final",
            "content": "这个分支先讨论方案。",
        })

        assert notes_store.get_current() is None

        result = service.update_if_due()

        assert result["status"] == "updated"
        assert model.calls == 5

        current = notes_store.get_current()
        assert current is not None
        assert current.source_leaf_id == (
            sessions.get_current_branch()[-1].id
        )
        print("分支切换后重建，不沿用旧分支水位：通过")


if __name__ == "__main__":
    main()