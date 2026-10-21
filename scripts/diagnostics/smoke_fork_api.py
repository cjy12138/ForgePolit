from forgepilot.paths import PROJECT_ROOT
"""真实 API 验收：只读 Fork 任务；会产生模型调用费用。"""
import json
import re
import subprocess
import sys
from pathlib import Path
from uuid import uuid4


def main():
    project = PROJECT_ROOT
    workspace = project / ".forgepilot" / "fork-api-demos" / uuid4().hex
    workspace.mkdir(parents=True)
    (workspace / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (workspace / "test_calculator.py").write_text(
        "import unittest\nfrom calculator import add\n\n"
        "class TestAdd(unittest.TestCase):\n"
        "    def test_positive(self):\n        self.assertEqual(add(2, 3), 5)\n", encoding="utf-8",
    )
    originals = {name: (workspace / name).read_bytes() for name in ("calculator.py", "test_calculator.py")}
    params = {"tasks": [{"name": "review_tests", "agent_type": "test-reviewer",
                         "instruction": "读取 calculator.py 和 test_calculator.py，分析测试覆盖范围。只读，不运行命令，不修改文件。",
                         "skills": [], "permission_mode": "plan"}]}
    task = ("请只调用一次 fork_tasks，参数为 " + json.dumps(params, ensure_ascii=False)
            + "。子任务返回后用一段话汇总结论，不再调用其他工具。")
    command = [sys.executable, "-X", "utf8", "-m", "forgepilot.cli.app",
               "--workspace", str(workspace), "--mode", "coding", "--permission-mode", "default",
               "--allow", "fork_tasks"]
    print("验收工作区：", workspace, flush=True)
    try:
        result = subprocess.run(command, input=task + "\n/exit\n", capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=240,
                                cwd=project)
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        (workspace / "cli-output.txt").write_text(output, encoding="utf-8")
        raise RuntimeError(f"真实验收超时，记录保留在 {workspace}") from exc
    (workspace / "cli-output.txt").write_text(output, encoding="utf-8")
    print(output)
    if result.returncode:
        raise RuntimeError(f"CLI 退出码：{result.returncode}")
    match = re.search(r"会话 ID：\s*([0-9a-f]{32})", output)
    if match is None:
        raise RuntimeError("未找到真实会话 ID，验收未通过")
    jobs = list((project / ".forgepilot" / "sessions" / "forks" / match.group(1)).glob("*/job.json"))
    if len(jobs) != 1:
        raise RuntimeError("预期恰好创建一个 Fork 作业")
    state = json.loads(jobs[0].read_text(encoding="utf-8"))
    if state["tasks"][0]["status"] != "completed":
        raise RuntimeError("Fork 子任务尚未完成")
    if any((workspace / name).read_bytes() != content for name, content in originals.items()):
        raise RuntimeError("只读验收中的源文件被修改")
    print("真实 Fork 验收通过：子任务完成、源文件未修改。")
    print("完整终端记录：", workspace / "cli-output.txt")


if __name__ == "__main__":
    main()
