# 独立进程执行：mini-swe-agent 参考

用户最新澄清：仅参考执行核心，不参考整体结构。下述接口、Agent 和入口源码阅读保留为背景，不构成复制其架构的要求。ForgePilot 的接口、工厂和装配服务自身插件化需求。

核对日期：2026-09-27；固定提交 `04d809ceab9df28f9adaed044884180159172930`（代码版本 2.4.6）。

已读源码：
- https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/__init__.py
- https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/default.py
- https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/environments/local.py
- https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/run/mini.py

核实：Environment 用 Protocol 声明 execute、get_template_vars、serialize 及 config；入口先创建 model/env，再传给 Agent；DefaultAgent 保存 self.env 并通过它执行 action。LocalEnvironment 使用 Pydantic 配置、命令字符串及结构化字典结果。该版本实际 subprocess 使用 shell=True；POSIX 超时杀进程组，Windows 分支仅 process.kill()，不能据注释宣称 Windows 后代进程全部清理。

ForgePilot 采用外部创建/注入环境、Protocol 结构化接口、后续配置工厂与环境配置记录。保留多工具注册表：Agent → ToolRegistry → 命令工具 → Environment。partial 是本项目把环境绑定到工具的办法，不是 mini 源码的装配方式。

当前 argv/shell=False、ExecutionResult、stdout/stderr 分离保持兼容；以后容器环境需要解释器与工作区路径映射。环境配置不得把所有环境变量或凭据写入提示词/轨迹。完成判断继续由 Harness 与执行证据负责，不照搬命令输出标记作为完整验收。

执行核心的后续重点：每次启动独立进程；显式指定工作目录、环境变量和超时；验证上次进程的 cd/export 不延续而文件修改保留；收集退出码和输出；处理超时与进程清理，再在隔离环境中验证。无状态仅指不依赖持久 Shell 状态，不表示文件、任务历史或容器每次清空。现有接口和注入代码无需回退；不要为了模仿 mini 继续扩展整体装配结构。
