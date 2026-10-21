# mini-swe-agent 源码学习映射

> 参考范围已更正：主要实现参考为 MiniCode。本文保留为辅助阅读记录，mini-swe-agent 仅用于自动修复执行方式参考；下文原有全项目参考与目录提案不再作为当前教学要求。

核对日期：2026-09-26。参考提交：04d809ceab9df28f9adaed044884180159172930，源码版本字段为 2.4.6。以下结论基于目录树及列出的核心文件，不代表已审查整个仓库。

固定源码入口：https://github.com/SWE-agent/mini-swe-agent/tree/04d809ceab9df28f9adaed044884180159172930/src/minisweagent

## 已阅读与借鉴点

参考范围覆盖全项目具体实现，不局限于本表列出的已读文件。后续按模块继续核对异常分类、重试、配置装配、工具协议解析、日志、CLI、测试和依赖管理等实现；未读部分只列为待读，不据此作功能判断。适合的实现优先借鉴，保留本项目既定功能与权限边界。

| 来源 | 源码行为 | ForgePilot 的应用 |
| --- | --- | --- |
| `__init__.py` | Model、Environment、Agent Protocol | 学习小型接口与依赖注入；接口放独立模块，避免导入时创建配置等副作用 |
| `agents/default.py` | run→step→query→execute_actions；步数、费用、时长限制；格式异常处理 | 将教学循环拆分成可读的方法；最终由 Harness 统一管理运行约束 |
| `agents/default.py` | messages 线性追加；每轮 finally 保存轨迹；serialize 包含配置与统计 | 完整轨迹、配置和退出原因持久化；扩展为树形会话，不照搬线性历史作为最终结构 |
| `models/litellm_model.py` | query、动作解析、观察消息格式化；发送前去除内部 extra 元数据；分类重试 | 模型协议转换与内部运行数据分离，不把轨迹全部字段发送给模型 |
| `environments/local.py` | 显式 cwd/env/timeout，独立子进程；POSIX 超时清理进程组 | 学习命令环境接口，明确 Windows 差异；不将本地执行称为沙箱 |
| `environments/docker.py` | 启动持久容器，每次 docker exec 启动命令，环境序列化与清理 | 参考自动修复环境生命周期；额外落实权限隔离、资源限制和超时后容器内进程清理 |
| `config/default.yaml` | observation_template 对超过 10000 字符的输出保留首尾各 5000 字符，提示缩小查询或重定向 | 学习输出预算与反馈；扩展为完整结果外存和可授权读回 |

## 上下文与记忆的边界

已读 DefaultAgent 将 self.messages 直接交给模型适配器，使用线性历史；已读 LitellmModel 做协议清理及缓存标记，没有在这些路径中看到自动历史摘要或长期记忆检索。default.yaml 的工具输出截断按字符，不是 Token 统计，也不是整个会话压缩。

轨迹持久化不等于长期记忆；提示缓存不等于上下文压缩。我们不能将计划中的长期记忆或树形会话宣称为从 mini-swe-agent 现成功能复用。

继续以 Pi 的树形会话设计和 Anthropic 上下文工程方法为参考，实现完整轨迹与模型上下文分离、摘要、分支范围、长期经验筛选及授权召回。其他文件/扩展是否提供相关能力，应阅读后再作结论。

## 目录方向（目标，不提前创建空模块）

```text
src/forgepilot/
  agents/          # AgentLoop、两种执行策略
  models/          # 模型 API 适配
  environments/    # 执行环境
  config/          # 类型化配置与提示模板
  run/             # CLI 启动与依赖装配入口
  harness/         # 路由、预算、审批、任务管理
  plugins/         # 注册、依赖与生命周期
  tools/           # 文件、编辑、命令等工具
  retrieval/       # 项目检索适配
  sessions/        # 树形会话及运行事件
  context/         # 模型上下文构建与压缩
  memory/          # 长期记忆及权限过滤
  auth/            # 身份与授权
  api/             # HTTP 入口
```

每课按“参考文件和方法→职责与取舍→本项目实现→验收”组织。现有 exercises/lesson01.py 继续作为教学验证；完成最小循环和协议后逐步迁移到正式包，不推翻已完成练习。只在需要对应功能时创建模块。

借鉴源码不减少已经承诺的功能；若实际复制或改编代码，需核对源仓库许可证并保留所需声明。
