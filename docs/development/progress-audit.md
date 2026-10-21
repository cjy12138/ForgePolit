# 当前实现与课程核对（2026-09-27）

> 本文以下为早期课程记录，不代表当前状态。2026-10-08 已补齐独立 Docker bugfix、恢复入口、三层压缩及共享 Markdown 记忆读写。当前 bugfix 能力和边界见 [使用与验收](../guides/bugfix.md)；身份、仓库授权和插件体系继续开发，界面已按用户要求取消。

范围：当前 Python 核心、入口、练习和学习计划；未读取 .env，未调用真实模型 API。本次运行 10 个离线入口均通过：check_tool_registration、check_tool_batch（5 项）、check_task_routing、check_task_interpreter_offline、check_harness、check_environment_injection、check_edit_tool、check_command_tool、check_process_state、deepseek_model。通过现有检查不代表覆盖所有边界。

## 已学内容

Agent 循环、单个/批量顺序工具调用、响应与参数校验、DeepSeek 适配；读取/列举/搜索/精确编辑/Python 执行；结构化退出码；任务理解及有限格式重试；路由与最小 Harness；CLI 澄清收集；环境接口、外部注入、工厂与注册冲突检查；独立进程状态实验，均已落到代码。

runtime.py 和 check_runtime.py 不存在：这是用户澄清 mini 参考范围后明确暂停的提案，不算漏做。整体架构不按 mini 迁移。

## 已复现并修复的问题

后续课程验收：以下两项已由用户修复，check_edit_tool 的五项检查全部通过。读取和编辑统一保留原始换行；唯一匹配改为从 first + 1 检测第二个匹配起点。

1. tools/files.py 用 read_text 默认转换 CRLF 为 LF，而 tools/edit.py 用 newline="" 保留原始换行。临时文件 b'a\r\nb\r\n' 经 read_file 读出，再作为 old_text 提交时失败。需统一读取/编辑换行策略，保留写回格式；加入 Windows 多行编辑回归检查。
2. tools/edit.py 的 str.count 只数不重叠匹配。文件 aaa、old_text=aa 时计为一次并改成 ba；实际存在两个起点，与“唯一精确匹配”约定不符。需检测第二个起点，包括重叠匹配。

## 教学中间态与后续补齐

- local.py 超时只抛文本错误，丢失已产生的 stdout/stderr；当前不保证清理后代进程，按执行核心课程继续完善。
- 命令继承父进程环境，cwd 不是权限边界；后续显式环境配置、凭据隔离、容器及运行时权限需完成。当前只运行可信教学代码。
- grep/命令输出先完整缓存在内存再截断，完整产物未存储；输出管理阶段实现有界采集和授权读回。
- coding/bugfix 当前是路由标签，共用本地执行器；尚未形成不同策略和隔离环境。
- completed 当前表示模型结束，不是独立测试验收；测试范围、代码版本、补丁与证据绑定待实现。
- 批量调用已有每批 16 个限制，max_steps 仍只计执行阶段模型请求；工具总次数、Token/费用/总时长预算、取消待补齐。
- 精确编辑仍直接写文件，没有原子替换、版本冲突检查或回滚；写入可靠性与恢复阶段完善。
- ToolContext 当前只有 workspace；身份、项目、能力及审批来自可信后端的设计尚未实现。
- CLI 澄清循环存在，但现有 Harness 检查未覆盖真实 input 循环的取消、空输入和上限；留作入口测试待办。
- 多工具调用后续持久化和压缩必须完整保留整组调用/结果。

## 后续功能没有取消

插件生命周期及初始化失败清理；树形 JSONL 会话与游标/任务状态；预算、分层输出、分块摘要与合并、超限恢复；Git 检查点；双模式与容器；API/身份授权/审计；界面与取消恢复；四类长期记忆的来源、验证、召回、失效和权限。学习计划仍保留全部范围。

推荐接续：上述两个编辑问题已验收 → 超时诊断输出与执行清理 → 按计划完善插件生命周期并进入会话持久化。无需重做已验收模块。
