# 从零完成第一个 v3 任务

本教程演示一次完整闭环：厚规划、Go 后执行、真实验证、归档和收尾。实际一行修复通常不需要建包；这里仅为展示完整路径。

[文档索引](README.md) · [Documentation index](README-en.md) · [根 README](../README.md) · [Root README](../README-en.md)

## 准备

按 [安装说明](../README.md) 安装 Spec Harness 1.0.0，并准备 Git、Python 3.9+ 和 Bash。创建一个干净练习仓库：

```bash
mkdir -p /tmp/spec-practice/tests && cd /tmp/spec-practice
git init -b main
printf 'def add(a, b):\n    return a - b\n' > calc.py
printf 'import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n' > tests/test_calc.py
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
  - verify: `python3 -B -m unittest discover -s tests -v`
  - expect: green
```

反复运行规划门禁，直到所有阻塞项为零，再由用户确认 Go：

```bash
python3 "$SPEC" plan-lint --static
python3 "$SPEC" plan-go
```

`plan-go` 会记录基线和 Git 绑定。此后不要重开规划对话，只按磁盘任务推进。

## 2. 执行与验证

把 `return a - b` 改为 `return a + b`，然后执行当前任务的真实命令：

```bash
python3 "$SPEC" next
python3 "$SPEC" verify task-add
```

`runs.jsonl` 记录命令、退出码、输出摘要和树指纹。验证后修改代码必须重跑对应任务；手工勾选不能替代证据。

## 3. 收尾

```bash
python3 "$SPEC" check
python3 "$SPEC" done
```

`done` 会生成完成报告，按用户在 Charter `V4` 中的预授权决定提交、合并或推送。只有授权为 `push` 时才会执行推送。遇到阻塞时使用：

```bash
python3 "$SPEC" decide "需要用户确认的外部选择" --fork F1 --reversal "不可逆"
python3 "$SPEC" learn "当 add 输出为 5" "不要重新实现运算符；优先检查测试输入"
```

完成后，归档保留 `spec.md`、`tasks.md`、`runs.jsonl`、状态和完成报告，是下一轮恢复与审计的真源。
