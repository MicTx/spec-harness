# 从零完成第一个 v3 任务

本教程演示标准包的规划、Go、验证和交付。小型可逆修复也可使用[原地快路径](../references/exec.md)，不自动提交。

[文档索引](README.md) · [Documentation index](README-en.md) · [根 README](../README.md) · [Root README](../README-en.md)

## 准备

按 [安装说明](../README.md) 安装 Spec Harness，并准备 Git、Python 3.9+、pytest 和 Bash。创建一个干净练习仓库：

```bash
mkdir -p /tmp/spec-practice/tests && cd /tmp/spec-practice
git init -b main
printf 'def add(a, b):\n    return a - b\n' > calc.py
printf 'from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n' > tests/test_calc.py
git add . && git commit -m "test: add failing addition example"
SPEC="$HOME/.claude/skills/spec/scripts/spec.py"
```

## 1. 规划

在支持 Skill 的宿主中直接输入：

```text
$spec 修复 add 函数，保持行为由现有测试定义
```

规划阶段先做只读侦察、确认意图并逆推任务失败点，然后生成 `.spec/specs/<slug>/spec.md` 与 `tasks.md`，内容必须符合 [`references/format.md`](../references/format.md)。核心是：

```markdown
## Goal
- goal: 修复加法结果
- literal ask: 修复 add
- done means: 现有测试通过
## Scope
- in: calc.py
- out: 新依赖
## Criteria
- C1 | 加法行为正确 | task-add
```

任务必须写清边界和红绿可判定的验证：

```markdown
- [ ] 修复加法
  - id: task-add
  - boundary: calc.py
  - verify: `python3 -m pytest -q tests/test_calc.py`
```

反复运行规划门禁，直到所有阻塞项为零，再由用户确认 Go：

```bash
python3 "$SPEC" plan-lint --static
python3 "$SPEC" plan-go
```

`plan-go` 会一次性记录基线、冻结时间和 Git 绑定。此后不要重开规划对话，也不能再次 Go，只按磁盘任务推进。在 `main`/`master`/`develop`/`release/*` 等受保护分支上 Go 时，harness 不会原地切换分支，而是在 `.spec/worktrees/<slug>` 建出 `spec/<slug>` 的 linked worktree、把包移进去并在那棵干净树里记基线——当前工作区及其未提交改动保持原样，不夹带进 spec；多个包因此可以各自在自己的 worktree 里并发执行。规划 lint 若在迁移后仍未过，进入该 worktree 里重跑 `plan-go` 即可就地冻结。

## 2. 执行与验证

把 `return a - b` 改为 `return a + b`，然后执行当前任务的真实命令：

```bash
python3 "$SPEC" next
python3 "$SPEC" verify task-add
```

`runs.jsonl` 记录命令、退出码、输出摘要和树指纹。`verify` 只接受依赖已完成的 ready 任务；最终 `check` 必须在交付树上重新执行声明的验证，手工勾选不能替代证据。同一失败签名连续出现四次时，任务会自动进入 `[!]`，修复根因后用 `unblock` 显式恢复：

```bash
python3 "$SPEC" unblock task-add --reason "已定位并修复根因"
```

## 3. 收尾

```bash
python3 "$SPEC" check
```

通过后自动复验最终树、归档、提交，并按 V4 交付。不通过时按 `next` 修复并回到同一个 `check`；`--check-only` 仅诊断。若正式验收已通过但 Git 移交不可达，agent 会给出等待或仅本地提交的选项，不把修复工作交给用户。详见[恢复循环](../references/exec.md)。记录已选分岔时使用：

```bash
python3 "$SPEC" decide --fork F1
```

完成后，归档保留 `spec.md`、`tasks.md`、`runs.jsonl`、状态和完成报告，是下一轮恢复与审计的真源。
