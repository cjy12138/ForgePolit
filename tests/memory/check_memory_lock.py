from pathlib import Path
from tempfile import TemporaryDirectory

from filelock import Timeout

from forgepilot.memory.store import MarkdownMemoryStore


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory) / "memory"

        first = MarkdownMemoryStore(root)
        second = MarkdownMemoryStore(root)

        with first.write_lock():
            # 同一个对象可以嵌套获取。
            with first.write_lock():
                pass

            # 另一个对象不能同时获得同一目录的锁。
            try:
                with second.write_lock():
                    pass
            except Timeout:
                pass
            else:
                raise AssertionError("第二个对象不应获得锁")

        # 外层退出后，其他对象可以获得锁。
        with second.write_lock():
            pass

        print("同一对象嵌套获取：通过")
        print("同一目录拒绝竞争写入：通过")
        print("退出后释放锁：通过")


if __name__ == "__main__":
    main()