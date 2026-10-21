# 工具模块：结合 Pico 的受控执行链

参考《2-pico》PDF 第 49–57 页的工具层设计。保持 ForgePilot 的
Pydantic/JSON Schema 注册协议、权限系统和原文产物机制。

## 能力对应

| Pico | ForgePilot | 结合方式 |
| --- | --- | --- |
| list_files | list_files | 保留直接子项列举与 200 项限制 |
| read_file | read_file | 兼容字符分页，增加 start_line/end_line 行范围 |
| search | grep_files | 保留 ripgrep 字面搜索、忽略规则和超时 |
| write_file | write_file | 新增创建、显式整文件覆盖，最多 1 MiB UTF-8 |
| patch_file | edit_file | 保留唯一精确匹配，含重叠匹配拒绝 |
| run_shell | run_command / run_python | 通用 Bash 仍限定 Docker；本地 Python 保留环境抽象 |
| delegate | fork_tasks | 保留独立子会话、权限继承、工具限制和递归禁止 |

## 执行顺序

注册名称检查 → Pydantic 参数校验 → 连续重复调用保护 → 原权限服务
（规则、工具强制检查、权限模式、AI／人工确认）→ handler 的文件状态与
内容检查 → 实际执行 → 审计结果 → Agent 保存工具消息与完整输出产物。

工具的实时文件内容检查仍在授权后进行。例如 edit_file 的唯一原文匹配、
write_file 的覆盖检查不绕过既有授权流程；Schema 负责先拒绝类型、行范围等非法输入。
不使用简单 risky 布尔值取代已有的分层权限策略。

## 使用示例

```json
{"name":"read_file","arguments":{"path":"src/main.py","start_line":10,"end_line":40,"limit":6000}}
{"name":"write_file","arguments":{"path":"src/new_module.py","content":"def add(a, b):\n    return a + b\n"}}
```

行号从 1 开始，结束行包含在范围内。省略 end_line 时最多读取 200 行；
limit 仍然是原文字符预算。若选定范围被截断，返回可继续读取的字符 offset。
行号仅用于展示，不能作为 edit_file 的 old_text。已有 offset/limit 调用不受影响。

write_file 默认拒绝覆盖现有文件。整文件覆盖需要 overwrite=true，并使用同目录临时文件
替换，避免失败时提前清空原文件。写入归类为编辑工具，受 plan、accept_edits、路径规则、
受保护文件检查约束。导入 Skill 时可以声明 write_file；已有 Skill 不会自动扩大工具集合。

## 重复调用保护

对工作区、当前任务、工具名以及填充默认值后的参数生成指纹，允许连续执行两次，
第三次起返回未执行错误并记录 reason_code=repeated_tool_call。
参数顺序不同、显式填写默认值不会绕过指纹。
其他工具实际执行后打断连续计数；审批拒绝不消耗执行次数。
每次 Agent.run 或显式 resume 重置计数，子 Agent 注册表使用独立计数。
这是一项本进程内的空转保护，不是跨重启幂等保证；中断恢复仍由原会话机制处理。

## 命令环境

LocalEnvironment 与 ripgrep 使用显式环境变量白名单，保留 PATH、临时目录、
用户目录、系统路径、语言区域和常用运行时位置，不继承 API 密钥、代理凭证、
PYTHONPATH 等任意父进程变量。Docker 命令沿用容器镜像自身环境。
依赖自定义环境变量的项目需要单独配置执行环境。
白名单降低凭证随环境变量泄漏的风险，不构成宿主机沙箱；本地 Python 仍具有当前用户的系统权限。

## 与参考方案的取舍

不额外改造记忆、会话或上下文模块，不新增简单的输出 clip 丢弃原文。
大输出继续由已有工具产物存档和上下文 Snip 处理；未接入产物／上下文控制器的独立注册表调用
仍返回完整结果。Fork 保持现有可配置工具边界，不强行把所有子 Agent 改成只读。

验证：`python -X utf8 -m tests.tools.check_tool_guards`。
