import os
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.environments.local import LocalEnvironment


def main():
    if os.name != "nt":
        print("此检查仅针对 Windows")
        return

    environment = LocalEnvironment()

    with TemporaryDirectory(prefix="forgepilot-tree-") as directory:
        workspace = Path(directory)
        marker = workspace / "child_finished.txt"

        child_code = """
import time
from pathlib import Path

print("child-ready", flush=True)
time.sleep(6)
Path("child_finished.txt").write_text("still alive", encoding="utf-8")
"""

        parent_code = """
import subprocess
import sys
import time

subprocess.Popen([
    sys.executable,
    "-X",
    "utf8",
    "-c",
    sys.argv[1],
])

print("parent-ready", flush=True)
time.sleep(15)
"""

        result = environment.execute(
            argv=[
                sys.executable,
                "-X",
                "utf8",
                "-c",
                parent_code,
                child_code,
            ],
            cwd=workspace,
            timeout_seconds=2,
        )

        # 等过子进程原本写文件的时间点。
        time.sleep(7)

        assert result.timed_out
        assert "parent-ready" in result.stdout
        assert "child-ready" in result.stdout
        assert "CLEANUP_WARNING" not in result.stderr, result.stderr
        assert not marker.exists(), "超时后子进程仍执行了写文件"

        print("超时后，测试子进程未继续执行延迟写入：通过")


if __name__ == "__main__":
    main()