import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from forgepilot.environments.local import LocalEnvironment


def main():
    environment = LocalEnvironment()

    # 使用随机名称，避免与宿主机已有环境变量冲突。
    variable_name = "FORGEPILOT_TEST_" + uuid4().hex.upper()

    with TemporaryDirectory(prefix="forgepilot-state-") as directory:
        workspace = Path(directory).resolve()
        (workspace / "child").mkdir()

        # 第一个进程：设置变量、写入文件、切换目录。
        first_code = """
import os
import sys
from pathlib import Path

os.environ[sys.argv[1]] = "created-in-first-process"
Path("proof.txt").write_text("saved", encoding="utf-8")
os.chdir("child")

print("first process finished")
"""

        first = environment.execute(
            argv=[
                sys.executable,
                "-X",
                "utf8",
                "-c",
                first_code,
                variable_name,
            ],
            cwd=workspace,
            timeout_seconds=10,
        )

        assert first.returncode == 0, first.stderr

        # 第二个进程：观察当前变量、目录和文件。
        second_code = """
import json
import os
import sys
from pathlib import Path

print(json.dumps({
    "variable": os.environ.get(sys.argv[1]),
    "cwd": str(Path.cwd()),
    "file_content": Path("proof.txt").read_text(encoding="utf-8"),
}))
"""

        second = environment.execute(
            argv=[
                sys.executable,
                "-X",
                "utf8",
                "-c",
                second_code,
                variable_name,
            ],
            cwd=workspace,
            timeout_seconds=10,
        )

        assert second.returncode == 0, second.stderr

        observed = json.loads(second.stdout)

        assert observed["variable"] is None
        print("上次进程的临时环境变量不延续：通过")

        assert Path(observed["cwd"]).resolve() == workspace
        print("每次从指定工作目录启动：通过")

        assert observed["file_content"] == "saved"
        print("文件修改在两次执行之间保留：通过")


if __name__ == "__main__":
    main()