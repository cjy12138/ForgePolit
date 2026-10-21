"""临时仓库中验收修复、恢复与补丁应用；不调用 API。"""
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests.integration.check_bugfix_runtime import SequenceModel, tool_call
from tests.bugfix.check_bugfix_workspace import git
from forgepilot.bugfix_apply import prepare_apply, apply_fix
from forgepilot.bugfix_service import BugfixService
from forgepilot.bugfix_tasks import load_bugfix_task
from forgepilot.sessions.budget.context_budget import ContextBudget
from forgepilot.sessions.models import MessageEntry
from forgepilot.sessions.store import SessionStore


class ScriptModel(SequenceModel):
    max_output_tokens = 2048

    def estimate_input_tokens(self, *, messages, tools):
        return 1000

    def generate(self, *, messages, tools):
        if self.calls == len(self.responses):
            raise KeyboardInterrupt
        return super().generate(messages=messages, tools=tools)


def main():
    with TemporaryDirectory(prefix="forgepilot-bugfix-recovery-") as directory:
        root = Path(directory)
        source = root / "source"
        source.mkdir()
        original = "def add(a, b):\n    return a - b\n"
        test_code = "import unittest\nfrom calculator import add\nclass TestAdd(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
        (source / "calculator.py").write_text(original, encoding="utf-8")
        (source / "test_calculator.py").write_text(test_code, encoding="utf-8")
        (source / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        git(source, "init")
        git(source, "add", ".")
        git(source, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "initial")
        tasks = root / "tasks"
        budget = ContextBudget(context_window=32000, max_output_tokens=2048, safety_margin=2000)

        def service(model):
            return BugfixService(model=model, source=source, tasks_root=tasks, budget=budget)

        def interrupted_task():
            previous = set((tasks / "_runs").iterdir()) if (tasks / "_runs").exists() else set()
            model = ScriptModel([
                tool_call("before", "run_command", {"command": "python -m unittest -v"}),
                tool_call("read", "read_file", {"path": "calculator.py"}),
                tool_call("edit", "edit_file", {"path": "calculator.py", "old_text": "return a - b", "new_text": "return a + b"}),
            ])
            try:
                service(model).run("修复 add，只修改 calculator.py，不修改测试文件。先运行 unittest，修复后重新运行。")
            except KeyboardInterrupt:
                pass
            else:
                raise AssertionError("没有模拟中断")
            run_dir, = set((tasks / "_runs").iterdir()) - previous
            saved = load_bugfix_task(tasks, run_dir.name)
            assert (saved.task.path / "calculator.py").read_text(encoding="utf-8") != original
            return saved

        saved = interrupted_task()
        baseline = saved.task.baseline_commit_id
        result = service(ScriptModel([
            tool_call("after", "run_command", {"command": "python -m unittest -v"}),
            {"type": "final", "content": "已修复并复测。"},
        ])).resume(saved.task.id)
        assert result["status"] == "completed"
        assert result["task_workspace"] == str(saved.task.path)
        assert load_bugfix_task(tasks, saved.task.id).task.baseline_commit_id == baseline
        store = SessionStore(saved.run_dir / "session.jsonl")
        outputs = {entry.payload.get("tool_call_id"): entry.payload for entry in store.get_current_branch()
                   if isinstance(entry, MessageEntry) and entry.payload.get("role") == "tool"}
        assert outputs["before"]["exit_code"] == 1 and outputs["after"]["exit_code"] == 0
        assert (source / "calculator.py").read_text(encoding="utf-8") == original
        assert (saved.task.path / "test_calculator.py").read_text(encoding="utf-8") == test_code
        print("中断后复用原副本与基线，保留失败测试并完成复测：通过")

        # 已完成的任务不再调用业务模型。
        completed = service(ScriptModel([])).resume(saved.task.id)
        assert completed["status"] == "completed" and completed["n_calls"] == 0
        plan = prepare_apply(saved.run_dir)
        assert b"calculator.py" in plan.patch and b"test_calculator.py" not in plan.patch
        assert (source / "calculator.py").read_text(encoding="utf-8") == original
        backup = apply_fix(plan)  # 只对本测试创建的临时仓库应用。
        assert backup.commit_id
        verification = subprocess.run(
            ["git", "-C", str(source), "diff", "--", "test_calculator.py"],
            check=True, capture_output=True,
        )
        assert not verification.stdout
        tests = subprocess.run(["python", "-m", "unittest", "-v"], cwd=source, capture_output=True)
        assert tests.returncode == 0, tests.stderr
        print("完成任务不重复请求模型；补丁预览、应用及实际 unittest：通过")

        # 模拟命令已产生副作用，但结果尚未落盘就中断。
        unknown = interrupted_task()
        counter = unknown.task.path / "counter.txt"
        counter.write_text("once\n", encoding="utf-8")
        unknown_store = SessionStore(unknown.run_dir / "session.jsonl")
        unknown_store.append_message({
            "role": "assistant", "type": "tool_call", "id": "missing_receipt",
            "name": "run_command", "arguments": {"command": "printf 'again\\n' >> counter.txt"},
        })
        before = unknown_store.path.read_bytes()
        with patch("forgepilot.bugfix_service.start_bugfix_runtime") as start:
            blocked = service(ScriptModel([])).resume(unknown.task.id)
            assert blocked["status"] == "needs_recovery"
            start.assert_not_called()
        assert unknown_store.path.read_bytes() == before
        recovered = service(ScriptModel([
            tool_call("inspect_counter", "read_file", {"path": "counter.txt"}),
            {"type": "final", "content": "已检查当前文件，旧命令执行情况未知。"},
        ])).resume(unknown.task.id, recover_unknown=True)
        assert recovered["status"] == "completed"
        assert counter.read_text(encoding="utf-8") == "once\n"
        receipts = [entry.payload for entry in unknown_store.get_current_branch()
                    if isinstance(entry, MessageEntry) and entry.payload.get("tool_call_id") == "missing_receipt"]
        assert len(receipts) == 1
        assert json.loads(receipts[0]["content"])["status"] == "execution_unknown"
        print("缺失结果先拒绝执行，显式恢复只标未知、不重放旧命令：通过")

        try:
            load_bugfix_task(tasks, "../outside")
        except ValueError:
            pass
        else:
            raise AssertionError("非法任务 ID 未被拒绝")
        print("拒绝非法任务 ID：通过")


if __name__ == "__main__":
    main()
