# ForgePilot

用 Python 实现的命令行编程 Agent，支持代码检索、文件编辑、测试执行和多轮工具调用，重点处理长任务中的上下文增长、会话中断恢复、记忆复用与工具权限控制。

主入口为交互式 coding，另提供独立任务副本与 Docker bugfix 流程。核心循环和树形会话参考 Pi，bugfix 命令执行方式参考 mini-swe-agent。

## 功能

- **工具执行**：Pydantic / JSON Schema 参数、统一执行与结果记录；目录包含 30 个工具名称，各模式按环境提供工具。
- **会话恢复**：JSONL 树形消息、分支切换、Git 检查点；缺失工具结果时暂停恢复，避免盲目重放。
- **上下文管理**：按 token 预算调度 Snip、Collapse 与前缀摘要，完整工具输出独立存档并支持分段读回。
- **记忆复用**：Markdown 主题与精选索引、轮次提取、会话笔记与 AutoDream；按任务选择主题并支持搜索补查。
- **Skill**：Markdown Skill 导入、选择、依赖工具配置、提取与同名更新；按任务保存配置快照。
- **子 Agent**：通过 fork_tasks 派发一个子任务，独立消息和压缩记录，同步返回结论。
- **权限审查**：静态规则、路径边界、权限模式、AI 审查、人工确认与脱敏审计。

这是个人工程项目。模型输出 final 不自动等于任务通过验收；子 Agent 当前为单任务同步执行，本地 coding 也不等于容器沙箱。

## 环境

Python **3.12+**，建议使用 3.13。Git 用于检查点和 Git 查询，ripgrep (`rg`) 用于源码内容搜索。真实模型执行需要 DeepSeek API Key；Docker 仅为容器 bugfix 所需，默认离线检查不需要 API 或 Docker。

## 安装

在克隆后的项目根目录执行；目前推荐源码或可编辑安装。

```bash
python -m venv .venv
```

```powershell
# Windows PowerShell 激活
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux 激活
source .venv/bin/activate

# 激活后安装
python -m pip install -e .
```

将 `.env.example` 复制为 `.env`，填写真实密钥和账号实际可用的模型名。不要提交 `.env`。

```dotenv
DEEPSEEK_API_KEY=your_api_key
DEEPSEEK_MODEL=deepseek-chat
```

## 使用

```bash
python -m forgepilot --help

# 指向自己的代码库
python -m forgepilot coding --workspace /path/to/repo --mode coding

# 使用本地演示工作区；首次启动从干净模板创建，不覆盖已有文件
python -m forgepilot coding --mode coding --permission-mode default
```

可编辑安装后也可使用 `forgepilot coding ...`。交互输入 `/exit` 退出，`/skill list` 查看 Skill。

```text
读取 calculator.py 和已有测试，解释 add 的实现，运行 unittest 并报告结果，不修改文件。
```

工具调用仍受权限策略约束，命令执行可能需要人工确认。

```bash
python -m forgepilot coding --resume SESSION_ID
python -m forgepilot coding --list-sessions
python -m forgepilot skills --workspace /path/to/repo list
python -m forgepilot permissions --workspace /path/to/repo list
python -m forgepilot bugfix --help
python -m forgepilot apply-fix --help
```

Docker bugfix 需要有 HEAD 提交的 Git 仓库和包含依赖的镜像，详见 [Bugfix 指南](docs/guides/bugfix.md)。

## 离线检查

```bash
# 递归发现 unittest，不调用 API 或启动 Docker
python -X utf8 -m tests

# 显式运行 10 个 main/assert 流程检查
python -X utf8 -m tests --suite smoke

# 单独检查工具层
python -X utf8 -m tests.tools.check_extended_tools
```

默认发现不会自动执行每个文件的 main。CI 使用同样的 unit/smoke 命令，见 [测试说明](docs/testing.md)。

## 目录

```text
ForgePilot/
├── forgepilot/             # 核心代码
│   ├── cli/               # coding、bugfix、补丁交付入口
│   ├── tools/             # 文件、命令、Git、记忆与 Fork 工具
│   ├── sessions/          # 树形会话、压缩与上下文控制
│   ├── memory/            # 召回、提取、笔记与 AutoDream
│   ├── skills/            # Skill 与配置快照
│   ├── fork/              # 子任务状态、会话与执行
│   ├── permissions/       # 规则、边界、审查与审计
│   ├── checkpoints/       # Git 检查点
│   ├── environments/      # 本地/Docker 执行及任务副本
│   └── models/            # 模型适配、用量与估算
├── tests/                 # 按功能分组的检查
├── examples/              # Skill、Agent 定义和干净工作区模板
├── scripts/               # 诊断与源码打包
├── docs/                  # 指南、架构、参考和开发记录
├── exercises/             # 旧命令兼容层
├── .github/workflows/     # 离线 CI
├── .env.example           # 配置模板
└── pyproject.toml         # 依赖和 CLI 入口
```

`.forgepilot/`、本地 demo 工作区、`.env`、output/tmp、.local 和缓存不提交。目录整理没有搬动已有会话或工作区。

## 文档

- [文档导航](docs/README.md)
- [项目结构与链路](docs/architecture/overview.md)
- [30 个工具](docs/guides/tools-catalog.md)
- [子 Agent](docs/guides/fork.md)
- [Bugfix](docs/guides/bugfix.md)
- [测试说明](docs/testing.md)
- [GitHub 发布前检查](docs/github.md)

历史 `python -m exercises.run_deepseek_agent` 和 `python -m exercises.check_*` 在源码目录仍可使用；新增代码优先使用正式入口。
