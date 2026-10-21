"""导出可供检查/上传的源码包；明确允许列表，不扫描或打包本地数据目录。"""
from pathlib import Path
import re
from zipfile import ZipFile, ZIP_DEFLATED

from forgepilot.paths import PROJECT_ROOT

DIRECTORIES = ("forgepilot", "tests", "scripts", "examples", "docs", "exercises", ".github")
FILES = ("README.md", "CONTRIBUTING.md", "pyproject.toml", "requirements.txt", ".env.example", ".gitignore")
EXCLUDED = {"__pycache__", ".forgepilot", ".git", ".venv", ".local", "node_modules"}


def source_files():
    result = [PROJECT_ROOT / name for name in FILES if (PROJECT_ROOT / name).is_file()]
    for name in DIRECTORIES:
        result.extend(path for path in (PROJECT_ROOT / name).rglob("*")
                      if path.is_file() and not path.is_symlink()
                      and not any(part in EXCLUDED for part in path.relative_to(PROJECT_ROOT).parts)
                      and path.suffix not in {".pyc", ".pyo", ".log"}
                      and not path.name.startswith(".env"))
    return sorted(set(result))


def main():
    paths = source_files()
    for path in paths:
        # 基础检查；不能代替人工审阅及 GitHub secret scanning。
        text = path.read_text(encoding="utf-8-sig")
        if re.search(r"\bsk-[A-Za-z0-9_-]{24,}", text):
            raise ValueError(f"疑似密钥，请检查后再打包：{path.relative_to(PROJECT_ROOT)}")
    target = PROJECT_ROOT / "output" / "releases" / "ForgePilot-source.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, "ForgePilot/" + path.relative_to(PROJECT_ROOT).as_posix())
    print(f"已导出 {len(paths)} 个源码/文档文件：{target}")


if __name__ == "__main__":
    main()
