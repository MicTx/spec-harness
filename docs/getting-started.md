# 从零完成第一个任务

本教程用默认 **LITE** 流程完成一次小型练习：建包、填写范围和任务、修复代码、验证、检查并归档。实际项目中的一行修复通常不需要建包；这里仅为演示完整路径。

[文档索引（中文）](README.md) · [Documentation index (English)](README-en.md) · [根 README（中文）](../README.md) · [Root README (English)](../README-en.md)

## 准备

按 [README 的安装说明](../README.md)安装默认 `core`，并准备 Git、Python 3.9+ 和 Bash。以下以 Claude Code 的安装路径为例；请在同一终端运行全部命令。其他宿主的安装路径见 [README](../README.md)。

创建全新练习仓库。若 `/tmp/spec-practice` 已存在，请换一个新目录，不要覆盖已有数据。

```bash
mkdir /tmp/spec-practice
cd /tmp/spec-practice
git init -b main
mkdir tests
printf 'def add(a, b):\n    return a - b\n' > calc.py
printf 'import unittest\nfrom calc import add\n\nclass AddTests(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n' > tests/test_calc.py
git add calc.py tests/test_calc.py
git commit -m "test: add failing addition example"
ROOT="$PWD"
SPEC="$HOME/.claude/skills/spec"
```

如果 Git 提示缺少用户名或邮箱，请先配置本练习仓库的 Git 身份。以下所有脚本从安装目录运行，`--root` 始终指向练习仓库。

## 1. 创建任务包

```bash
python3 "$SPEC/scripts/init_spec_package.py" \
  --root "$ROOT" --slug 2026-11-04_fix-add-function --title "Fix add function"
```

默认生成 `spec.md` 和 `tasks.md`，并在 Git 仓库中创建 `spec/2026-11-04_fix-add-function` 分支。若工作树不干净或分支已存在，先处理提示，不要强制覆盖。

## 2. 填写范围与任务

打开 `.spec/specs/2026-11-04_fix-add-function/spec.md`，保留模板的标题、格式标记、`Workflow: LITE`、`evidence: runs` 和生成的 Git 分支，填写以下八项即可：

| 项 | 示例 |
| --- | --- |
| 项目目标 | 修复 `add` 返回错误结果的问题 |
| 关键假设 | 现有测试表达预期行为 |
| 待确认问题 | 无 |
| 不在范围内 | 不新增接口或依赖 |
| 最小实现路径 | 将减法改为加法 |
| 5.1 快速检查 | `python3 -B -m unittest discover -s tests -v` |
| 6. 成功标准 | `- [ ] 现有加法测试通过 -> verify: task_0001` |
| 风险与约束 | 本例只覆盖现有测试样例 |

`5.1` 的 `范围级别` 保持 `package`；需要更广覆盖时按 [模板说明](../references/templates.md)声明 `integration` 或 `project` 并填写对应检查。不要留下模板占位内容。

把 `tasks.md` 的模板任务换成一项，保留文件标题和使用规则：

```markdown
## 阶段一：实现
- [ ] 修复 add 并运行现有测试
  - boundary: 只改 calc.py 和必要的回归测试
  - verify: `python3 -B -m unittest discover -s tests -v`
```

第一项任务自动编号为 `task_0001`，与 §6 对应。每项任务都需要 `boundary` 和一条可执行的 `verify`；有先后顺序时使用 `depends-on`。更多任务约定见 [模板说明](../references/templates.md)。

## 3. 实现并验证

将示例项目里的 `calc.py` 中的 `return a - b` 改为 `return a + b`，然后通过 Spec 执行所有任务的验证：

```bash
python3 "$SPEC/scripts/spec_verify.py" run \
  --root "$ROOT" --slug 2026-11-04_fix-add-function --all
```

应看到该任务通过并写入 `runs.jsonl`。只有真实验证通过后，才将 `tasks.md` 的任务和 `spec.md` 第 6 节成功标准勾为 `[x]`。直接运行测试不会生成 Spec 的机器验证记录；验证后修改代码、任务命令或其他受指纹覆盖的文件时，需要重跑 `--all`。

## 4. 检查与归档

```bash
python3 "$SPEC/scripts/check_spec_package.py" \
  --root "$ROOT" --slug 2026-11-04_fix-add-function
```

若检查失败，根据输出修复问题；新增可执行问题追加到 `tasks.md`，然后重新运行 `$SPEC/scripts/spec_verify.py run ... --all` 和检查。检查通过后归档：

```bash
python3 "$SPEC/scripts/complete_spec_package.py" \
  --root "$ROOT" --slug 2026-11-04_fix-add-function --archive
```

归档目录为 `.spec/specs/archive/2026-11-04_fix-add-function/`，其中包含 `spec.md`、`tasks.md`、`runs.jsonl` 和生成的 `completion-summary.md`。归档脚本不提交代码；需要提交时先检查变更，再单独提交并在消息中写 `Spec: 2026-11-04_fix-add-function`。合并、推送及删除分支属于单独的收尾动作，须取得授权。

## 接下来

- 在支持 Skill 的宿主中，可使用 `/spec:new`、`/spec:run`、`/spec:check` 和 `/spec:done`；脚本的完整契约见 [命令索引](../references/commands.md)。
- 当前会话中断后，从活动包的 `spec.md`、`tasks.md` 和 `runs.jsonl` 恢复；`handoff.md` 如有也应阅读。
- 权限、不可逆操作、真实偏好和外部依赖需要用户决定；其他问题先自主调查。涉及认证、账务、密钥、数据迁移或破坏性操作时使用 `--workflow full`，见 [模板说明](../references/templates.md)。
