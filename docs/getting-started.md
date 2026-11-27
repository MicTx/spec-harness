# 从零完成第一个 v3 任务

本教程演示标准包的规划、Go、验证和交付。小型可逆修复也可使用[原地快路径](../references/exec.md)，不自动提交。

[文档索引](README.md) · [Documentation index](README-en.md) · [根 README](../README.md) · [Root README](../README-en.md)

## 准备

按 [README 的源码安装说明](../README.md#安装)安装 `main`，准备 Git、Python 3.9+、pytest 和 Bash。本教程使用 Claude Code 的默认 Skill 路径；其他宿主请把 `SPEC` 改成对应路径。创建一个新的练习仓库（Git 需已配置提交身份）：

```bash
practice=$(mktemp -d)
cd "$practice"
mkdir tests
git init -b main
printf 'def add(a, b):\n    return a - b\n' > calc.py
printf 'from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n' > tests/test_calc.py
git add calc.py tests/test_calc.py
git commit -m "test: add failing addition example"
SPEC="$HOME/.claude/skills/spec/scripts/spec.py"
SLUG="$(date +%F)_fix-add"
```

## 1. 规划

在 Claude Code 中输入 `/spec:plan 修复 add 函数，保持行为由现有测试定义`（支持 `$spec` 的宿主也可用技能入口）。由宿主规划时不必手写任务包：用宿主给出的工作区路径继续。想直接用 CLI，可以按下面的最小示例创建草稿：

```bash
mkdir -p ".spec/specs/$SLUG"
```

将以下内容保存为 `.spec/specs/$SLUG/spec.md`：

```markdown
<!-- spec-format: 3 -->

## Goal
- goal: 修复加法结果
- done means: 现有测试通过

## Scope
- in: calc.py
- out: 新依赖

## Criteria
- C1 | 加法行为正确 | task-add
```

将任务保存为 `.spec/specs/$SLUG/tasks.md`。`expect: red` 会在 Go 时运行基线探测，确认测试能复现当前错误：

```markdown
- [ ] 修复加法
  - id: task-add
  - boundary: calc.py
  - verify: `python3 -m pytest -q tests/test_calc.py`
  - expect: red
```

规划门禁通过后执行 Go。宿主如果已完成 Go，跳过这两条，使用它返回的工作区：

```bash
python3 "$SPEC" --slug "$SLUG" plan-lint --static
python3 "$SPEC" --slug "$SLUG" plan-go
```

`plan-go` 会一次性记录基线、冻结时间和 Git 绑定。此后不要重开规划对话，也不能再次 Go，只按磁盘任务推进。在 `main`/`master`/`develop`/`release/*` 等受保护分支上 Go 时，harness 不会原地切换分支，而是在 `.spec/worktrees/<slug>` 建出 `spec/<slug>` 的 linked worktree、把包移进去并在那棵干净树里记基线——当前工作区及其未提交改动保持原样，不夹带进 spec；多个包因此可以各自在自己的 worktree 里并发执行。规划 lint 若在迁移后仍未过，进入该 worktree 里重跑 `plan-go` 即可就地冻结。**若 Go 输出了 `worktree` 路径，先切换到该目录，再执行后续的 `verify` 和 `check`；这些命令默认针对当前目录绑定的包。若没有创建 worktree，则继续留在项目目录。**

## 2. 执行与验证

把 `return a - b` 改为 `return a + b`，然后执行当前任务的真实命令：

```bash
python3 "$SPEC" next
python3 "$SPEC" verify task-add
```

`runs.jsonl` 记录命令、退出码、失败签名和树指纹。`verify` 只接受就绪任务，手工勾选不能替代验证。重复同一签名四次，或同一任务累计失败八次，会按单任务门禁进入 `[!]`；有新证据后用 `unblock` 恢复。这个计数与 [Stop 的自动续跑规则](loop-continuity.zh.md)无关：没有新证据时不必等到计数用完。

```bash
python3 "$SPEC" unblock task-add --reason "已定位并修复根因"
```

## 3. 收尾

```bash
python3 "$SPEC" check
```

`check` 在实际要交付的代码树上复验，成功后归档、创建交付提交，再按冻结的 V4 收尾。这里未指定远端交付，默认本地提交。不通过时按 `next` 修复并重跑同一个 `check`；`--check-only` 只预检，不运行验收。真实项目若授权的 Git 移交在归档前受阻，才可能出现等待或仅本地提交的决策。详见[恢复循环](../references/exec.md#delivery-recovery)。

完成后，归档保留 `spec.md`、`tasks.md`、`runs.jsonl`、状态和完成报告，是下一轮恢复与审计的真源。
