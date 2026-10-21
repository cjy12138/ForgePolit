from forgepilot.paths import PROJECT_ROOT
from contextlib import ExitStack
from pathlib import Path

from forgepilot.environments.docker import DockerEnvironment


def main():
    project_root = PROJECT_ROOT

    environment = DockerEnvironment(
        workspace=project_root / "demo_workspace",
    )

    with ExitStack() as lifecycle:
        lifecycle.callback(environment.close)
        environment.start()

        assert environment.container_id is not None
        print("容器已启动：", environment.container_id)

    assert environment.container_id is None
    print("容器关闭流程完成。")


if __name__ == "__main__":
    main()