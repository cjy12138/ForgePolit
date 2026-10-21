# ForgePilot 工具手册：30 个工具

本手册对应项目当前支持的 30 个工具名称，其中 15 个为此次新增。工具目录是整个项目的能力全集；某次模型请求中实际提供哪些工具，由运行模式、Skill、子 Agent 定义和会话恢复快照共同决定。

## 1. 从哪里使用

coding 工具池已经接入此次新增的 15 个工具，bugfix 的任务副本工具池也已接入。子 Agent 从父运行时工具池中选择，仍受自身基础工具、Skill 和权限限制，不会自动拥有所有工具。

`run_command` 需要 Docker bugfix 环境，只在提供了该执行环境时可用；默认 coding 不提供它。`fork_tasks` 在主会话派发入口可用，子 Agent 不允许递归派发。因此“目录共 30 个”不表示每次请求都发送 30 个定义。记忆、笔记、产物工具还需要绑定对应存储。

新工具沿用原有 Registry：模型参数 → Pydantic 校验 → 重复调用检查 → 权限决策 → handler → ToolResult → Agent 的会话记录。每个新增工具注册了权限检查回调；参数中的每一条路径还会经过强制边界检查，静态 allow 和 bypass 也不能绕过。

已经开始的任务恢复时使用原 Skill/工具快照，新工具不会悄悄加入旧快照。请开启新任务，或显式使用声明了新工具的 Skill。

### 快速试用

```powershell
# 在项目根目录启动；plan 模式仅允许已分类的只读能力
.\.venv\Scripts\python.exe -X utf8 -m forgepilot.cli.app --mode coding --permission-mode plan
```

在交互界面输入：

```text
使用 project_info 和 directory_tree 了解当前工作区，再用 glob_files 找到 Python 文件，
用 python_outline 列出 calculator.py 的函数，最后使用 python_check 检查语法。
不要修改文件，不运行项目代码。若文件不存在，请按实际目录调整路径。
```

也可以导入本次附带的只读 Skill：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m forgepilot.skills --workspace "D:\program\ForgePilot\demo_workspace" add examples/skills/repository_inspection/SKILL.md
```

启动相同工作区的 coding 会话后输入 `/skill repository_inspection`。该 Skill 的工具依赖覆盖仓库结构、Python 静态检查和 Git 查询，不修改文件。非 Git 工作区跳过 Git 步骤。

下文代码块都是工具参数 JSON，由模型生成并提交给工具，不是 PowerShell 命令。路径相对于当前工作区。

## 2. 工具总览

| 序号 | 名称 | 主要用途 | 来源 |
|---|---|---|---|
| 1 | read_file | 分段或按行读取一个文件 | 已有 |
| 2 | list_files | 查看目录直接子项 | 已有 |
| 3 | grep_files | 搜索文件正文中的字面文本 | 已有 |
| 4 | edit_file | 唯一精确匹配后局部替换 | 已有 |
| 5 | write_file | 新建或显式整文件覆盖 | 已有 |
| 6 | run_python | 执行 Python 命令及测试 | 已有 |
| 7 | run_command | 在 Docker bugfix 环境执行 Bash | 已有 |
| 8 | read_artifact | 读回完整历史工具输出 | 已有 |
| 9 | read_memory | 读取长期记忆主题正文 | 已有 |
| 10 | glob_memory | 按主题文件名查找记忆 | 已有 |
| 11 | grep_memory | 搜索记忆内容 | 已有 |
| 12 | list_memory_versions | 列出主题历史版本 | 已有 |
| 13 | read_memory_version | 读取某个旧版 Markdown | 已有 |
| 14 | read_session_notes | 分段读取当前会话笔记 | 已有 |
| 15 | fork_tasks | 派发一个受控子 Agent 任务 | 已有 |
| 16 | glob_files | 按文件名/路径通配符递归找文件 | 新增 |
| 17 | directory_tree | 查看有界目录树 | 新增 |
| 18 | file_info | 查询类型、大小和修改时间 | 新增 |
| 19 | file_hash | 获取文件 SHA-256 | 新增 |
| 20 | read_files | 批量读取最多 8 个文件片段 | 新增 |
| 21 | compare_files | 比较两个文件内容 | 新增 |
| 22 | read_json | 解析 JSON 并按节点读取 | 新增 |
| 23 | python_outline | 提取类/函数位置 | 新增 |
| 24 | python_imports | 静态提取 import | 新增 |
| 25 | python_check | 检查 Python 语法 | 新增 |
| 26 | project_info | 识别项目类型和配置入口 | 新增 |
| 27 | git_status | 查询已暂存/未暂存/未跟踪状态 | 新增 |
| 28 | git_diff | 查看某文件工作区或暂存差异 | 新增 |
| 29 | git_log | 查询提交记录 | 新增 |
| 30 | git_show_file | 读取指定提交中的文件 | 新增 |

## 3. 已有工具的用途和调用方式

### 01. read_file：读一个文件

用于理解具体实现、查看错误行附近的内容。支持字符分页，也支持从 1 开始且包含结束行的按行读取；按行模式不能同时提供非零 offset。输出中的行号仅用于定位，不能作为 edit_file 的原文。

```json
{"path":"src/calculator.py","start_line":1,"end_line":60,"limit":6000}
```

参数：`path` 必填；`offset` 默认 0；`limit` 默认 1000、最大 20000 字符；`start_line/end_line` 可选。单文件最多 1 MiB，要求 UTF-8 文本。

### 02. list_files：列出一层目录

快速确认文件是否存在，区分文件、目录和链接。只列直接子项，最多 200 项。深入了解结构时使用 directory_tree。

```json
{"path":"src"}
```

### 03. grep_files：搜正文

定位函数名、错误文本、配置项的实际出现位置，返回文件名、行号和匹配行。按字面文本匹配，不是正则表达式。依赖宿主机可用的 ripgrep，搜索遵循其默认忽略规则。

```json
{"path":"src","pattern":"context_controller"}
```

参数：`pattern` 必填，`path` 默认 `.`。与 glob_files 的区别是：grep 查内容，glob 查名称。

### 04. edit_file：局部编辑

对已经读取的文件进行精确替换。`old_text` 必须非空且唯一匹配，找不到或出现多次都会失败，避免误改。`new_text` 可以为空，表示删除这段文本。

```json
{"path":"calculator.py","old_text":"return a - b","new_text":"return a + b"}
```

这是写操作，plan 拒绝；accept_edits 可放行符合边界的编辑，其他情况由规则和权限模式决定。

### 05. write_file：新建或整体替换

默认仅创建新文件，已有文件必须显式设置 `overwrite=true`。可自动创建父目录，内容上限 1 MiB，不允许空字符。整文件覆盖采用同目录临时文件替换；局部修改优先使用 edit_file。

```json
{"path":"docs/design.md","content":"# Design\n","overwrite":false}
```

### 06. run_python：执行 Python

运行已有测试、脚本或 Python 模块。参数以数组传入，不是任意 shell 字符串；执行仍可能有副作用，需要命令权限检查。

```json
{"args":["-m","unittest","discover","-v"],"timeout_seconds":15}
```

`timeout_seconds` 默认 15、范围 1–60 秒。coding 使用本地执行环境，bugfix 使用对应 Docker 环境。需要同时看工具状态和退出码：进程正常返回但退出码 1，可能是测试失败。

### 07. run_command：容器命令

为 bugfix 提供更通用的命令入口，如已有构建、测试脚本。使用 Docker 环境中的 Bash，镜像必须实际提供 Bash 及所需工具链。不会因为调用这个工具自动安装依赖。

```json
{"command":"python -m unittest discover -v","timeout_seconds":30}
```

`timeout_seconds` 默认 30、范围 1–120 秒。命令结构分析和权限检查仍生效，默认 coding 工具池不提供本工具。

### 08. read_artifact：读回历史输出

Snip 等处理缩短了展示内容后，根据工具结果中真实的 artifact_id 分段读取完整输出。只允许读取当前分支能引用的产物，不能随意猜 ID 读取其他会话数据。

```json
{"artifact_id":"0123456789abcdef0123456789abcdef","offset":0,"limit":2000}
```

示例 ID 是占位值，实际必须使用已有引用。`limit` 最大 8000 字符。

### 09. read_memory：读取主题

读取召回候选或搜索结果中的 Markdown 主题正文。不是读取工作区的任意 Markdown 路径，主题文件名必须符合记忆库命名规则。

```json
{"filename":"testing_preferences.md","offset":0,"limit":2000}
```

自动召回和手动补读共享会话累计读取额度。记忆是参考资料，不能授予权限，也不能代替对当前代码的核实。

### 10. glob_memory：按名称找主题

已知主题名称的一部分时快速查找，例如部署、测试、用户偏好相关主题。候选不限于 MEMORY.md 已收录主题。

```json
{"pattern":"*testing*.md"}
```

具体参数上限以工具 Schema 为准。

### 11. grep_memory：搜记忆正文

名称或 description 不足以定位时，按关键字补查长期记忆，例如错误码、框架名称、用户要求。不会搜索普通项目源码。

```json
{"keyword":"unittest"}
```

### 12. list_memory_versions：查旧版本目录

当主题发生过 extract/AutoDream 更新时，列出已保存版本的信息，再按实际返回的 version_id 读取。

```json
{"filename":"testing_preferences.md","offset":0,"limit":20}
```

`limit` 最大 50。没有版本不代表工具失败。

### 13. read_memory_version：读旧主题

读取旧版 Markdown，用于理解某次偏好或项目约定如何变化。和读取某条来源会话消息是不同能力。

```json
{"filename":"testing_preferences.md","version_id":"extract/0123456789abcdef0123456789abcdef","offset":0,"limit":2000}
```

version_id 必须来自真实版本列表，前缀为 extract 或 dream。`limit` 最大 8000 字符，也受当前记忆正文预算约束。

### 14. read_session_notes：读当前进度笔记

获取当前分支可用的结构化任务笔记，适合确认目标、已完成工作和待办。会话笔记不等于跨会话长期主题；没有笔记时会明确返回错误。

```json
{"offset":0,"limit":2000}
```

### 15. fork_tasks：派发子 Agent

把边界明确的分析任务交给独立子会话，主 Agent 接收结论。当前一次只支持一个子任务，同步执行；不宣称自动并行。

```json
{"tasks":[{"name":"inspect_python","instruction":"静态分析 calculator.py 的函数与语法，不修改文件。","base_tools":["read_file","python_outline","python_check"],"permission_mode":"plan"}]}
```

可选 `skills` 预加载 Skill，或使用 `agent_type` 指定定义。权限只能按规则继承或收窄，子 Agent 禁止再次调用 fork_tasks。它有自己的消息、状态和压缩记录，但共享工作区并不提供文件隔离。

## 4. 本次新增的 15 个工具

### 16. glob_files：按名称递归查找

用途：找测试文件、配置文件、特定模块，避免逐层 list_files。

```json
{"path":"src","pattern":"**/*.py","max_results":100}
```

`path` 默认 `.`；`pattern` 默认 `*`；`max_results` 默认 100、最大 500。匹配工作区内指定目录下的相对路径或文件名，支持常见 `*.py`、`**/*.py` 写法；使用 fnmatch 语义，不承诺等同完整 shell glob 语法。

返回 `files` 和 `limit_reached`。默认递归深度 8，跳过隐藏项、依赖目录和链接；不是 gitignore 的完整实现。

### 17. directory_tree：目录树

用途：以一次调用了解多层结构，先定位再读取，减少盲读。

```json
{"path":"src","max_depth":3,"max_entries":200}
```

深度默认 3、最大 12；条目默认 200、最大 500。返回每项的路径、类型和相对深度。达到条目上限会标记 `limit_reached`；达到设定深度不代表更深处没有文件。

### 18. file_info：文件元信息

用途：在读取前确认目标类型、字节数和修改时间，判断是否适合普通文本读取。

```json
{"path":"src/calculator.py"}
```

返回 `path/type/bytes/modified_at`。目录的 bytes 是文件系统目录项元信息，不是递归统计的总大小。修改时间是 UTC，不证明内容一定变化或未变化。

### 19. file_hash：内容指纹

用途：比较文件版本、确认上下文中的文件是否仍是同一内容；不会把整个文件正文放进上下文。

```json
{"path":"src/calculator.py"}
```

对最多 1 MiB 的普通文件返回字节数和 SHA-256，可处理二进制。哈希相同只说明读取到的字节相同，不代替权限授权或证明某个操作执行过。

### 20. read_files：批量读取

用途：同时读取实现和测试、几个相关模块，减少串行工具往返。

```json
{"paths":["src/calculator.py","tests/test_calculator.py"],"per_file_chars":2000}
```

最多 8 个路径；每个文件默认 2000、最大 5000 字符，单文件最多 1 MiB。正文合计最多 16000 字符，整体输出还有公共上限，超长会显示 TRUNCATED。

返回每项 `path/content/truncated` 和 `omitted_files`。任一路径不合法会拒绝整个调用；某文件不存在或不是文本时返回失败，不将其伪造为空文件。需要精确续读时改用 read_file。

### 21. compare_files：比较两个现有文件

用途：比较修复版与备份、两个配置、两份生成结果；不要求 Git 仓库。

```json
{"left":"calculator.py","right":"backup/calculator.py","context_lines":3}
```

`context_lines` 默认 3、最大 10。两侧均要求不超过 1 MiB 的 UTF-8 文本；返回 unified diff 或“文件内容相同”。本工具比较两个当前文件，git_diff 比较工作区/索引与 Git 基线。

### 22. read_json：读取 JSON 节点

用途：读取 package.json、测试数据、配置中的局部节点，避免让模型手动解析大量文本。

```json
{"path":"package.json","pointer":"/scripts/test"}
```

`pointer` 默认为空，表示整个 JSON。`/items/0/name` 表示数组节点，键中的 `/` 用 `~1` 转义，`~` 用 `~0` 转义。解析失败或路径不存在会返回失败；不会执行表达式。文件仍受 1 MiB 和输出上限限制。

### 23. python_outline：定位类与函数

用途：快速知道文件有哪些类、函数及其位置，再按行读取相关实现。

```json
{"path":"forgepilot/agent.py"}
```

返回 `symbols`，包含名称、AST 类型、起止行号；类内方法使用 `Class.method` 形式。最多 300 项，嵌套展开深度最多 30。提取模块、类和函数体直接包含的定义，不承诺覆盖 if/try 等所有控制流中的定义，不是完整语言服务器。

### 24. python_imports：依赖入口

用途：查看文件静态 import 关系，辅助寻找模型适配器、服务、工具模块。

```json
{"path":"forgepilot/agent.py"}
```

返回模块、导入名称和行号，最多 300 项。包含源码 AST 中的条件/局部 import；不执行 import、不检查包是否已安装、不解析动态 __import__/importlib 的运行结果。

### 25. python_check：语法检查

用途：编辑后快速发现括号、缩进、语法结构错误，不产生项目执行副作用。

```json
{"path":"calculator.py"}
```

返回 `syntax_valid`；语法错误时提供行号、列号和说明。语法无效属于检查结论，所以该工具调用本身可以正常返回；不能仅凭 ToolResult.ok 判断代码语法是否通过。

基于当前 Agent 的 Python AST 解析器，不 import 文件、不运行测试、不写 pyc。它只保证能被 AST 解析，不承诺所有编译上下文检查、类型检查、业务正确性或其他 Python 版本兼容性。

### 26. project_info：项目入口识别

用途：先判断代码库可能采用的语言和构建体系，避免没有依据就安装依赖或执行测试。

```json
{"path":"."}
```

检查指定目录直接包含的 pyproject.toml、requirements.txt、package.json、pom.xml、CMakeLists.txt、go.mod、Cargo.toml 等已知入口，返回存在的标记文件。只检查是否存在，不读取全部配置、不运行构建、不递归搜索 monorepo 所有子项目。

### 27. git_status：当前修改范围

用途：修改前掌握用户已有改动，修改后确认哪些文件发生变化。

```json
{"path":"src"}
```

`path` 默认 `.`。返回 porcelain 状态码和文件路径，最多展示 300 项；保留前导空格的两个字符分别表示索引与工作区状态。未跟踪目录可能按目录列出，重命名不做配对分析。过滤 .env、.git、.forgepilot 等受保护路径，不返回完整文件内容。

### 28. git_diff：单文件差异

用途：验证改动是否符合任务范围，读取模型修改和原实现之间的差异。

```json
{"path":"src/calculator.py","staged":false}
```

`staged=false` 比较工作区与索引，`true` 比较索引与 HEAD。只接受单个文件路径，目录需先用 git_status 定位；被删除文件路径也可查询。未跟踪文件不在普通 git diff 中出现。

禁用 external diff、textconv 和重命名检测。路径按字面处理，`*` 不会展开成整个仓库，防止路径通配符意外包含受保护文件。

### 29. git_log：提交线索

用途：找到某文件最近改动的提交，再读对应历史实现。

```json
{"path":"src/calculator.py","limit":10}
```

最多 50 条，返回短提交哈希、作者时间和提交标题。只读查询，不会 commit、checkout、reset、fetch 或 push；没有提交的仓库可能返回 Git 错误。

### 30. git_show_file：历史实现

用途：比较某个已知提交中的实现、理解回归前行为，不改变当前代码。

```json
{"path":"src/calculator.py","ref":"HEAD"}
```

`ref` 默认 HEAD，支持普通分支、标签和提交哈希；不接受 `--help`、`HEAD~1`、范围表达式等，先从 git_log 取真实哈希。仅返回指定提交中的普通文件，链接、目录、子模块和不存在路径会拒绝；含空字符的内容不作为文本返回。

与 Git 检查点恢复的区别：本工具只读取历史内容，不恢复文件、不移动会话游标。

## 5. 新工具共用边界和限制

1. 所有路径相对工作区。禁止绝对路径、逃逸路径、环境凭证文件和受保护目录；批量工具会检查所有路径。遍历时不跟随符号链接/junction，直接路径解析后也必须留在允许范围。
2. 新增文本/哈希读取单文件最多 1 MiB；公共输出最多 20000 字符，超过时追加 TRUNCATED。输出整体截断后不保证仍是完整 JSON，模型应缩小范围，不把残片当完整结构解析。
3. 目录遍历跳过隐藏项、.venv、venv、node_modules、__pycache__、dist、build；最多检查 10000 个目录项，超过会要求缩小范围。它没有完整复用 gitignore 规则。
4. Python AST 工具使用 Agent 进程的解析器，对极端复杂输入做基础错误处理，不是运行不可信代码的 OS 沙箱。
5. 四个 Git 查询在宿主机上针对当前工作区运行，只读；bugfix 中查询的是挂载任务副本，不是在容器中额外启动 git。依赖宿主机 git 可用，且当前工作区必须是仓库根目录，拒绝查询上级仓库。
6. Git 固定子命令、禁用 shell、分页器、fsmonitor、外部 diff/textconv；每次子进程最多 15 秒，输出先写临时文件，最多读回 64 KiB，展示还有 20000 字符公共上限。需要机器解析的 Git 状态列表超过 64 KiB 会报错而非解析残片。
7. 这些边界不提供并发文件系统事务，也不代表普通文件正文和提交标题已经自动脱敏。当前任务应只读取必要范围，项目中避免把密钥放入普通源码或提交信息。
8. 新增 15 个都是只读工具，plan 模式允许符合强制边界的查询；静态 deny 仍优先。已有写文件和命令工具继续使用原来的权限策略。
9. 多路径工具的静态 pattern 规则：allow 必须覆盖所有路径，deny/ask 命中任一路径即可生效。会话精确授权仍包含完整工具参数指纹，不会变成整个工具的永久授权。

## 6. 常见组合

### 了解陌生代码库

`project_info → directory_tree → glob_files → python_outline/python_imports → read_files`。

先识别入口，再定位符号和必要正文；信息不足时回到 read_file/grep_files。不要每次无条件调用整套流程。

### 检查一次 Python 改动

`git_status → git_diff → python_check → run_python`。

前三步可以只读执行，最后的测试属于命令执行，单独走权限确认。AST 通过不能代替测试通过。

### 分析回归

`git_log → git_show_file → read_file/git_diff`。

使用真实提交哈希读旧版本，比较当前逻辑，不需要 checkout，因此不会覆盖用户已有修改。

### 记忆与压缩后的证据补查

`glob_memory/grep_memory → read_memory → list_memory_versions/read_memory_version` 用于记忆；`read_artifact` 用于原始工具输出。文件工具用于当前工作区，不混淆三类数据源。

## 7. 代码位置和离线验收

| 模块 | 责任 |
|---|---|
| forgepilot/tools/extended.py | 装配 15 个扩展工具及检查回调 |
| forgepilot/tools/extended_catalog.py | 共享名称集合与多路径字段定义 |
| forgepilot/tools/workspace.py | 文件检索、批量读取、JSON 解析与比较 |
| forgepilot/tools/workspace_support.py | 路径检查、容量和遍历上限 |
| forgepilot/tools/code_inspection.py | AST 静态检查与项目标记识别 |
| forgepilot/tools/git_queries.py | 四个只读 Git 查询 |
| forgepilot/permissions/boundaries.py | 新工具全部路径的强制边界 |
| forgepilot/permissions/tool_checks.py | 只读分类，支持 plan 模式 |
| forgepilot/permissions/rules.py | 多路径静态规则语义 |
| forgepilot/skills/catalog.py | 统一 30 个已知名称，SkillStore 复用 |
| exercises/check_extended_tools.py | 文件/Git/权限/Skill 离线验收 |
| examples/skills/repository_inspection/SKILL.md | 可导入的只读 Skill |

```powershell
.\.venv\Scripts\python.exe -X utf8 -m tests.tools.check_extended_tools

.\.venv\Scripts\python.exe -X utf8 -m unittest tests.tools.check_extended_tools tests.tools.check_tool_guards tests.permissions.check_permission_tools tests.permissions.check_permission_audit tests.fork.check_fork_skills
```

验收使用临时工作区和真实临时 Git 仓库，覆盖全部 15 个新增工具、参数/输出限制、受保护路径、多路径规则、Git 查询不改变 HEAD/索引、超时映射与 Skill 导入。不请求模型 API，不启动 Docker；真实模型是否选择正确工具和 Docker 实际运行不包含在这些离线结论里。
