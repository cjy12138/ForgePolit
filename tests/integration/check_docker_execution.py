from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.environments.docker import DockerEnvironment


def main():
    with TemporaryDirectory(prefix="forgepilot-docker-") as directory:
        workspace = Path(directory)

        environment = DockerEnvironment(workspace=workspace)

        with ExitStack() as lifecycle:
            lifecycle.callback(environment.close)
            environment.start()

            first = environment.execute(
                argv=[
                    "python",
                    "-c",
                    (
                        "import os; "
                        "from pathlib import Path; "
                        "Path('result.txt').write_text('saved'); "
                        "os.environ['FORGEPILOT_TEST'] = 'yes'; "
                        "os.chdir('/tmp')"
                    ),
                ],
                cwd=workspace,
                timeout_seconds=15,
            )

            assert first.returncode == 0, first.stderr
            assert (
                workspace / "result.txt"
            ).read_text(encoding="utf-8") == "saved"
            print("容器写入挂载的任务文件：通过")

            second = environment.execute(
                argv=[
                    "python",
                    "-c",
                    (
                        "import os; "
                        "from pathlib import Path; "
                        "print(os.getcwd()); "
                        "print(os.getenv('FORGEPILOT_TEST')); "
                        "print(Path('result.txt').read_text())"
                    ),
                ],
                cwd=workspace,
                timeout_seconds=15,
            )

            assert second.returncode == 0, second.stderr
            assert second.stdout.splitlines() == [
                "/workspace",
                "None",
                "saved",
            ]
            print("进程状态不延续，文件状态保留：通过")

            timeout = environment.execute(
                argv=["python", "-c", "import time; time.sleep(20)"],
                cwd=workspace,
                timeout_seconds=1,
            )

            assert timeout.timed_out
            assert environment.container_id is None
            assert (workspace / "result.txt").exists()
            print("超时关闭容器，保留任务文件：通过")


if __name__ == "__main__":
    main()