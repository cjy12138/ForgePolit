from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, get_ident

from tests.memory.check_memory_service import FakeModel
from forgepilot.memory.background import BackgroundMemoryExtraction
from forgepilot.memory.extraction import MemoryExtractionAgent
from forgepilot.memory.service import MemoryExtractionService
from forgepilot.memory.store import MarkdownMemoryStore
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.models import SessionHeader
from forgepilot.sessions.store import SessionStore
from forgepilot.exceptions import ModelError

class BlockingModel(FakeModel):
    def __init__(self):
        super().__init__()
        self.started = Event()
        self.release = Event()
        self.thread_id = None

    def generate(self, *, messages, tools):
        self.thread_id = get_ident()
        self.started.set()

        # 等待检查代码允许它继续，不调用真实 API。
        if not self.release.wait(timeout=5):
            raise ValueError("检查未及时释放假模型")

        return super().generate(
            messages=messages,
            tools=tools,
        )


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)

        sessions = SessionStore(root / "session.jsonl")
        sessions.create(SessionHeader(
            owner_id="local-user",
            project_id="demo",
            cwd=str(root),
        ))

        sessions.append_message({
            "role": "user",
            "content": "以后优先使用 unittest。",
        })
        sessions.append_message({
            "role": "assistant",
            "type": "final",
            "content": "好的。",
        })

        memory_store = MarkdownMemoryStore(root / "memory")
        model = BlockingModel()

        agent = MemoryExtractionAgent(
            model=model,
            memory_store=memory_store,
            context_budget=ContextBudget(
                context_window=16000,
                max_output_tokens=1024,
                safety_margin=1000,
            ),
        )

        service = MemoryExtractionService(
            sessions=sessions,
            memory_store=memory_store,
            agent=agent,
        )
        worker = BackgroundMemoryExtraction(service)

        history_before = sessions.path.read_bytes()

        try:
            result = worker.start()
            assert result["status"] == "started"
            assert model.started.wait(timeout=2)

            # 此时后台被假模型暂停，主线程仍能执行。
            assert model.thread_id != get_ident()
            assert worker.poll()["status"] == "running"
            assert memory_store.list_filenames() == []
            assert not service.state_path.exists()

            print("后台生成期间主线程可以继续运行：通过")

            # 允许后台完成。
            model.release.set()

            # 检查脚本中直接等待 Future，确保任务已完成。
            future = worker._future
            assert future is not None
            future.result(timeout=2)

            # 完成的是候选生成，还没有正式发布。
            assert memory_store.list_filenames() == []
            assert not service.state_path.exists()

            print("后台完成后不自行发布：通过")

            result = worker.poll()

            assert result["status"] == "published"
            assert result["filenames"] == [
                "testing_preferences.md"
            ]
            assert service.state_path.exists()
            assert "unittest" in memory_store.read(
                "testing_preferences.md"
            ).content

            assert sessions.path.read_bytes() == history_before
            print("主线程发布并记录处理状态：通过")

            assert worker.poll()["status"] == "idle"
            assert worker.start()["status"] == "already_processed"
            assert model.calls == 1

            print("发布后同轮不重复提取：通过")

            # 新增一轮，测试生成过程中取消。
            sessions.append_message({
                "role": "user",
                "content": "以后不要默认引入 pytest。",
            })
            sessions.append_message({
                "role": "assistant",
                "type": "final",
                "content": "好的。",
            })

            history_before_cancel = sessions.path.read_bytes()

            memory_before_cancel = {
                path.name: path.read_bytes()
                for path in memory_store.root.glob("*.md")
            }
            state_before_cancel = service.state_path.read_bytes()

            # 重新让假模型停在请求过程中。
            model.started.clear()
            model.release.clear()

            assert worker.start()["status"] == "started"
            assert model.started.wait(timeout=2)

            assert worker.cancel()["status"] == "cancel_requested"
            assert worker.poll()["status"] == "cancelling"

            # 模拟当前 API 请求返回。
            model.release.set()

            future = worker._future
            assert future is not None

            try:
                future.result(timeout=2)
            except ModelError as exc:
                assert exc.code == "memory_extraction_cancelled"
            else:
                raise AssertionError("取消后应停止提取")

            assert worker.poll()["status"] == "cancelled"
            assert worker.poll()["status"] == "idle"

            # 正式主题、索引、处理记录和会话均未改变。
            assert {
                       path.name: path.read_bytes()
                       for path in memory_store.root.glob("*.md")
                   } == memory_before_cancel

            assert service.state_path.read_bytes() == state_before_cancel
            assert sessions.path.read_bytes() == history_before_cancel

            print("生成过程中取消，不发布且不记录成功：通过")

        finally:
            model.release.set()
            worker.close()


if __name__ == "__main__":
    main()