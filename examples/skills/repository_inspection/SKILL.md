---
name: repository_inspection
description: 静态了解项目结构、Python 符号和 Git 修改范围，不运行项目代码或修改文件。
tools:
  - project_info
  - directory_tree
  - glob_files
  - read_files
  - read_json
  - python_outline
  - python_imports
  - python_check
  - git_status
  - git_diff
  - git_log
  - git_show_file
---

1. 用 project_info 与 directory_tree 识别项目入口，缩小检索范围。
2. 用 glob_files 查找任务涉及的文件；用 read_files 批量读取有界片段。
3. Python 文件优先用 python_outline/python_imports 定位，再按需读取正文。
4. 如当前工作区是 Git 仓库根目录，用 git_status 定位变化，用 git_diff 查看具体文件。
5. 需要理解历史实现时使用 git_log 和 git_show_file；工作区没有 Git 时跳过这些步骤。
6. 检查 Python 语法可以用 python_check，但不能把语法通过说成测试通过。
7. 不修改文件、不运行项目代码。所有结论说明对应文件和实际验证范围。
