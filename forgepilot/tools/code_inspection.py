"""Python AST 和项目入口检查；不会 import 工作区模块。"""
import ast

from forgepilot.tools.workspace import FileInput, PathInput
from forgepilot.tools.workspace_support import checked_path, output, read_text


def _tree(context, path):
    try:
        return ast.parse(read_text(context, path), filename=path)
    except (RecursionError, MemoryError) as exc:
        raise ValueError("Python 源码结构过于复杂") from exc


def python_outline(*, context, path):
    try:
        tree = _tree(context, path)
    except SyntaxError as exc:
        raise ValueError(f"语法错误，第 {exc.lineno} 行：{exc.msg}") from exc
    items = []

    def visit(nodes, parent="", depth=0):
        if depth > 30:
            return
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{parent}.{node.name}" if parent else node.name
                items.append({"name": name, "kind": type(node).__name__, "line": node.lineno, "end_line": node.end_lineno})
                if len(items) >= 300:
                    return
                visit(node.body, name, depth + 1)
                if len(items) >= 300:
                    return

    visit(tree.body)
    return output({"symbols": items, "limit_reached": len(items) >= 300})


def python_imports(*, context, path):
    try:
        tree = _tree(context, path)
    except SyntaxError as exc:
        raise ValueError(f"语法错误，第 {exc.lineno} 行：{exc.msg}") from exc
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.append({"line": node.lineno, "module": None, "names": [item.name for item in node.names]})
        elif isinstance(node, ast.ImportFrom):
            imports.append({"line": node.lineno, "module": "." * node.level + (node.module or ""), "names": [item.name for item in node.names]})
        if len(imports) >= 300:
            break
    return output({"imports": sorted(imports, key=lambda item: item["line"]), "limit_reached": len(imports) >= 300})


def python_check(*, context, path):
    try:
        _tree(context, path)
    except SyntaxError as exc:
        return output({"syntax_valid": False, "line": exc.lineno, "column": exc.offset, "message": exc.msg})
    return output({"syntax_valid": True, "note": "仅通过 AST 语法解析，不等于测试通过或依赖可用。"})


def project_info(*, context, path="."):
    root = checked_path(context, path)
    if not root.is_dir():
        raise ValueError("项目路径不是目录")
    markers = {
        "Python": ("pyproject.toml", "requirements.txt", "setup.cfg", "pytest.ini"),
        "JavaScript/TypeScript": ("package.json", "tsconfig.json"),
        "Java": ("pom.xml", "build.gradle", "build.gradle.kts"),
        "C/C++": ("CMakeLists.txt", "Makefile"),
        "Go": ("go.mod",), "Rust": ("Cargo.toml",),
    }
    found = {language: [name for name in names if (root / name).is_file() and not (root / name).is_symlink()]
             for language, names in markers.items()}
    return output({"path": path, "markers": {key: value for key, value in found.items() if value},
                   "note": "只根据配置文件是否存在推断；不会安装依赖或执行构建。"})


CODE_SPECS = {
    "python_outline": (FileInput, python_outline, "通过 AST 提取 Python 类、函数及行号，不导入或执行源码。"),
    "python_imports": (FileInput, python_imports, "静态列出 Python import/from import 及行号，不验证依赖是否安装。"),
    "python_check": (FileInput, python_check, "用 AST 检查 Python 语法，不运行测试、不执行源码、不生成 pyc。"),
    "project_info": (PathInput, project_info, "根据根目录配置文件识别项目类型与配置入口，不读取凭证或运行构建。"),
}
