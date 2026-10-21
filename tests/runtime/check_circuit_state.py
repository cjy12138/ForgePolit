from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.sessions.control.circuit_state import CircuitStateStore


def main():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "demo.context.json"
        store = CircuitStateStore(path, "demo")

        assert store.load().consecutive_failures == 0

        store.save(3)
        reopened = CircuitStateStore(path, "demo")

        assert reopened.load().consecutive_failures == 3
        print("重新创建存储对象后保留失败次数：通过")

        reopened.save(0)
        assert CircuitStateStore(path, "demo").load().consecutive_failures == 0
        print("显式清零可以持久化：通过")

        try:
            CircuitStateStore(path, "another-session").load()
        except ValueError as exc:
            assert "不属于" in str(exc)
        else:
            raise AssertionError("应拒绝其他会话的状态")

        print("拒绝会话 ID 不一致：通过")

        path.write_text('{"broken":', encoding="utf-8")

        try:
            store.load()
        except ValueError:
            pass
        else:
            raise AssertionError("损坏状态不能按零次失败处理")

        print("损坏状态明确报错：通过")


if __name__ == "__main__":
    main()