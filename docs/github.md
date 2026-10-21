# GitHub 发布前检查

源码、测试、文档和示例已经分开。个人文件及运行数据仍保留在本机，通过 .gitignore 排除；本次没有初始化 Git 或上传远程仓库。

## 不提交的文件

.env、.forgepilot、.local、根目录 demo/demo_workspace/demo_skill_workspace、output/tmp、虚拟环境、IDE/编译缓存与日志。

.env.example 是配置模板。examples/workspaces/calculator 是干净示例，不复制现有本地权限和状态。历史面试/简历笔记移到 .local/archive，生成简历仍保存在已忽略的 output 下。

## 源码包

```bash
python -m scripts.package_source
```

输出 output/releases/ForgePilot-source.zip。使用源码目录允许列表并做基础密钥格式检查；不直接压缩整个工作目录，也不能用简单正则替代人工检查或 secret scanning。

## Git 提交

尚未初始化仓库时，在项目根目录执行：

```bash
git init
git add README.md CONTRIBUTING.md pyproject.toml requirements.txt .gitignore .env.example
git add forgepilot tests scripts examples docs exercises .github
git status --short
git diff --cached --stat
```

确认暂存内容后再 commit，并按照自己创建的 GitHub 仓库页面关联 remote 和 push。已有仓库不需重新初始化。曾经被跟踪的敏感文件不会因为新增 .gitignore 就自动从历史消失。

许可证由项目所有者选择，本次没有擅自指定。参考来源保留在 README 与 docs/references，不发布第三方凭证、私有资料或未经授权的完整源码。
