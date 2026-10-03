# 最小工作流 Harness 教学程序

`mini_harness.py` 使用 Python 标准库，适用 Python 3.9+。它把书里的最小骨架变成可运行练习：规格、任务依赖、验收和验证记录都能在隔离目录中看到；它不是完整 Agent 运行时。

## 使用

先将 `BOOK` 指向本书目录，再进入自己的隔离练习仓库：

```bash
export BOOK=/absolute/path/to/book
python3 "$BOOK/examples/mini_harness.py" init photo-migration
python3 "$BOOK/examples/mini_harness.py" route photo-migration
```

编辑 `.mini-harness/photo-migration/` 的三份 Markdown，完成真实范围定义后把 spec 中 `Status: draft` 改成 `Status: active`。任务字段 id/boundary/verify 必填，依赖字段 `depends-on` 是逗号分隔稳定 id；没有依赖就省略该字段。

完成任务后运行自己批准的测试，记录真实 evidence 再勾选。所有验收条目勾选后将 checklist 的 `Acceptance: pending` 改为 `Acceptance: passed`，添加 `Evidence: 实际结果与位置`。不要把示例文案当测试证据。

```bash
python3 "$BOOK/examples/mini_harness.py" check photo-migration
python3 "$BOOK/examples/mini_harness.py" verify photo-migration \
  --timeout 30 -- python3 -m unittest discover -s tests
```

verify 要求文档结构已通过。它只执行操作者在 `--` 后批准的 argv，不执行 Markdown 中的 verify 字符串，不自动勾选任务。

- route 输出 plan/execute/review/ready-to-archive，只读。
- check 返回 0 表示结构就绪，1 表示尚未完成，2 表示格式/IO/参数错误。
- verify 返回 0 表示命令成功且期间三件套未变，1 表示命令失败/超时/文档变化，2 表示无法启动或输入错误。
- `evidence.json` 记录最近一次命令的有限输出与三件套摘要；源代码是否变化仍需单独检查。

## 能力边界

- 合作式单写者；不提供多写者锁、不可变审计历史或敌对进程沙箱。
- argv 不经 Shell，但批准运行的程序仍有当前用户权限。不要向日志传入凭证。
- POSIX 超时终止进程组；Windows 仅直接子进程，不承诺整棵进程树隔离。
- 输出通过临时文件保存，报告最多保留 stdout/stderr 各 64 KiB；临时文件无磁盘配额，噪声程序须放到有配额的环境。
- 没有模型 API、照片业务、对象存储客户端、助手调度或 Git 发布逻辑。
- 创建中遇到磁盘错误可能留下未完整草稿；程序报错且不覆盖已有目录，恢复前应检查现场。

测试均运行在临时目录，不访问真实仓库或网络：

```bash
python3 -m unittest discover -s "$BOOK/examples" -v
```
