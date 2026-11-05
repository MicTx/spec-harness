# 从零完成第一个任务：保姆级教程

本文对应包含它的 Spec Harness 源码版本，主线固定使用 **Claude Code**。请使用所检出源码里的这篇教程；不同版本的文档和脚本不要混用。目标是让你亲手走完一条能复现的路：

安装 → 自检 → 建任务包 → 填写 `spec.md` / `tasks.md` → 真实运行 verify → 记录一次失败并修正 → 过门禁 → 归档提交 → 恢复演练。

其他宿主、UI 改动、偏离授权、项目级长路放在附录。第一次使用时，先只走主线，不要同时打开所有附录。

## 0. 你会得到什么

走完主线后，练习仓库里会留下：

- `.spec/specs/archive/2026-11-04_fix-add-function/spec.md`
- `.spec/specs/archive/2026-11-04_fix-add-function/tasks.md`
- `.spec/specs/archive/2026-11-04_fix-add-function/runs.jsonl`
- `.spec/specs/archive/2026-11-04_fix-add-function/completion-summary.md`
- 一个带 `Spec:` footer 的 Git 提交

其中 `runs.jsonl` 是机器证据，`completion-summary.md` 是交付总结。两者都不是手写的“完成声明”。

---

## 第一步：准备环境

你需要：

- Git 2.28 或更新版本（能使用 `git init -b main`）。
- Python 3.9 或更新版本。
- Bash。
- 一个支持 Skill 的宿主；本文用 Claude Code。

先确认版本：

```bash
git --version
python3 --version
bash --version
```

预期结果：三条命令都能输出版本号，且 Python 不低于 3.9。

创建一个只用来练习的仓库：

```bash
mkdir -p /tmp/spec-practice
cd /tmp/spec-practice
git init -b main
```

创建两个文件：

```bash
mkdir -p tests
cat > calc.py <<'PY'
def add(a, b):
    return a - b
PY
cat > tests/test_calc.py <<'PY'
import unittest
from calc import add


class AddTests(unittest.TestCase):
    def test_adds_two_numbers(self):
        self.assertEqual(add(2, 3), 5)


if __name__ == "__main__":
    unittest.main()
PY
```

先确认这个练习仓库真的有一个失败用例：

```bash
python3 -B -m unittest discover -s tests -v
```

预期结果：退出码为 1，输出里能看到 `FAILED` 和 `-1 != 5`。这正是后面要修的问题。

提交基线：

```bash
git add calc.py tests/test_calc.py
git commit -m "test: add failing add() regression"
git status --short
```

预期结果：`git status --short` 没有输出。没有输出，才说明下一步的 diff 可以归属于这项任务。

**失败时怎么办**

- `git init -b main` 报错：你的 Git 太旧。用 `git init` 后执行 `git branch -M main`。
- `python3` 不存在：先安装 Python 3.9+，或把命令换成你机器上真实的 `python3.x`。
- `git commit` 报用户名/邮箱未配置：先运行 `git config user.name "你的名字"` 和 `git config user.email "你的邮箱"`。

---

## 第二步：安装 Spec Harness

把包含本教程的源码下载到临时目录，并记录实际提交：

```bash
git clone https://github.com/MicTx/spec-harness.git /tmp/spec-harness
cd /tmp/spec-harness
test -f docs/getting-started.md && git rev-parse HEAD
```

预期结果：最后一行输出提交号。请在这份 checkout 中打开 `docs/getting-started.md`，后续命令也使用同一目录。若文件不存在，请停止并使用该版本自带的文档；例如 `v0.24.0` 早于本教程。要重现本次练习，可再次检出记录的提交号并使用其自带的教程。

安装到 Claude Code，并安装完整组件面：

```bash
INSTALL_PROFILE=full INSTALL_HOSTS=claude bash install.sh
```

为什么用 `full`：`autoplan`、`autorun`、`status`、`organize` 属于完整安装；默认 `core` 已有 `new` / `run` / `check` / `done` / `push` / `doctor`。

预期结果：安装输出里能看到 Skill 已安装、验证命令，以及 Claude Code 可用的 `/spec` 与 `/spec:<stage>` 命令。

先做一次安装自检：

```bash
python3 ~/.claude/skills/spec/scripts/smoke_test_spec_skill.py
```

预期结果：最后一行是 `smoke: OK`。

再检查宿主环境：

```bash
python3 ~/.claude/skills/spec/scripts/doctor_spec_environment.py \
  --root /tmp/spec-practice \
  --host claude
```

预期结果：输出是逐项检查；正常时会看到 layout、channels、host 等检查通过。`doctor` 只检查连接和文件，不代表模型已经 live 采用流程。

**失败时怎么办**

- 提示找不到 `scripts/smoke_test_spec_skill.py`：安装目录不对。用 `INSTALL_HOSTS=claude` 重装，并确认输出里的 Skill 路径。
- `doctor` 报告未安装或未接线：先按它给出的修复建议运行 `--fix`，再重跑。
- 宿主里看不到 `/spec`：重启宿主或新开一个会话，让 Skill 重新加载。
- 你只想先用 core：`doctor` 也在 core 中可用；后面的 `autoplan` / `autorun` 附录需要重新装 full。

---

## 第三步：创建任务包

回到练习仓库：

```bash
cd /tmp/spec-practice
```

Claude Code 里可以直接说：

```text
/spec:goal 修复 add 函数返回错误结果的问题，保留现有回归测试
```

本文为了让你看清每一步，改用等价的脚本入口：

```bash
python3 /tmp/spec-harness/scripts/init_spec_package.py \
  --root /tmp/spec-practice \
  --slug 2026-11-04_fix-add-function \
  --title "Fix add function"
```

预期结果：输出类似下面几行，并且当前分支变成 `spec/2026-11-04_fix-add-function`：

```text
branch: spec/2026-11-04_fix-add-function
created: /tmp/spec-practice/.spec/specs/2026-11-04_fix-add-function/spec.md
created: /tmp/spec-practice/.spec/specs/2026-11-04_fix-add-function/tasks.md
ready: /tmp/spec-practice/.spec/specs/2026-11-04_fix-add-function
```

确认一下：

```bash
git branch --show-current
ls .spec/specs/2026-11-04_fix-add-function
```

预期结果：分支名是 `spec/2026-11-04_fix-add-function`，目录里有 `spec.md` 和 `tasks.md`。

**失败时怎么办**

- 提示工作树不干净：先 `git status --short`，把无关改动提交、暂存或拆成另一个任务包。不要带着两个目标建包。
- 提示分支已存在：换一个 slug，或先确认那条分支属于哪个任务；不要强行覆盖。
- 提示不能从保护分支创建：先切回 `main`，再重试。

---

## 第四步：填写 `spec.md`

打开：

```text
.spec/specs/2026-11-04_fix-add-function/spec.md
```

把下面这份示例替换进去。它是尚未完成的 LITE 包，任务和成功标准要等真实验证通过后再勾选：

```markdown
# Fix add function - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：修复 add 函数返回错误结果的问题，并保留回归测试。
- **目标用户**：练习仓库的使用者。
- **核心价值**：让加法结果可信，并留下可恢复的验证记录。
- **Literal ask**: 修复 add(2, 3) 返回 -1 的问题。
- **Stated purpose**: 用最小改动修复一个纯函数缺陷。
- **Inferred outcome**: add(2, 3) 返回 5，单元测试通过；confidence: high；evidence: 现有测试与失败输出。
- **Constraints and exclusions**: 只改 calc.py 与 tests/test_calc.py；不改接口。
- **Trade-off preference**: correctness > speed

## 2. 假设与待确认

### 2.1 已确认事实
- 当前 `add` 返回 `a - b`。
- `tests/test_calc.py` 已覆盖 `add(2, 3) == 5`。

### 2.2 关键假设
- A1 | 失败只来自 `add` 的实现错误，不来自测试导入路径。 | kind: system | evidence: 直接运行 unittest 的失败输出 | CONFIRMED | if false: 先修测试导入路径。

### 2.3 待确认问题
- 无

### 2.4 可选解释与取舍
- 当前选择：修实现而不是改测试 -> 测试表达的是预期行为。

### 2.5 已证伪路径
- 无

<!-- spec:external-facts -->
### 2.6 外部事实
- 无

## 3. 功能范围

### 3.1 核心功能（MVP）
- [ ] 修复 add 并让现有测试通过。

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 不新增命令行接口或前端页面。
- 不改测试覆盖范围。

## 4. 最小实现路径
- 最简单可行方案：把 `a - b` 改为 `a + b`。
- 暂不引入：新依赖、重构或类型标注。
- 不做的抽象/配置化：不为单个函数增加配置层。

### 4.1 方案阶梯
- B0 is the minimal change.

Recommendation: B0 because the defect is a one-line implementation bug. Flips if A1 is FALSIFIED.

## 5. 技术决策
- Workflow: LITE
- 技术栈：Python 3 标准库 unittest
- 证据协议：evidence: runs
- 本轮允许改动：`calc.py`、`tests/test_calc.py`
- 本轮不应触碰：安装器、其他示例代码、Git 历史
- Autonomy: medium
- Git integration branch：`spec/2026-11-04_fix-add-function`

### 5.1 验证策略
- 范围级别：package
- UI surface: unchanged
- 变更对象：add 函数与其单元测试
- 快速检查：`python3 -B -m unittest discover -s tests -v`
- 集成检查：适用外：练习仓库没有其他跨模块路径
- 全项目检查：适用外：本轮不涉及共享基础设施或发布载荷
- 升级触发：只有出现跨模块回归或准备发布时才升级

<!-- spec:correction-log -->
### 5.5 修正日志
- none.

<!-- spec:deviation-log -->
### 5.6 偏离日志
- none.

## 6. 成功标准与验证方式
- [ ] 修复 add 并让现有测试通过 -> verify: task-0001

## 7. 风险与约束
- 风险点：只修一个样例，未覆盖负数或大数 -> 本轮只验证现有测试，扩展留给后续任务。
```

### 这几个字段为什么重要

- **Intent 五项**：先说清字面请求、目的、推断结果、约束和取舍。它们防止任务越做越偏。
- **A1 行**：`statement | kind | evidence | CONFIRMED|UNVERIFIED|FALSIFIED | if false`。假设被证伪时，改方案，不要硬写完成。
- **`Workflow: LITE`**：小任务用 LITE。LITE 允许 Intent 每项一行短语，B0 与 B1 相同时只写 `- B0 is the minimal change.`。
- **`UI surface: unchanged`**：这是全包必填字段。新模板中的占位值必须明确替换；本例是纯逻辑任务，所以写 `unchanged`。如果改了交互界面，按附录 B 写 `changed` 并绑定 Browser check。
- **`evidence: runs`**：机器任务的 verify 由 `scripts/spec_verify.py` 真实执行，并写入 `runs.jsonl`。手动跑测试不算机器证据。
- **修正日志**：失败轮必须写“新信息”。只有“又试了一次”不算新信息。

### 合格与不合格对照

不合格：

```markdown
- **项目目标**：优化一下代码。
- A1 | 应该没问题 | evidence: 感觉可以
```

问题：目标不可验证、假设没有证据、没有 `if false`。

不合格：

```markdown
- Workflow: FULL
- B0 | literal ask | 修 add
- B1 | minimal change | 改实现
- B2 | different approach | 只改测试
```

问题：`FULL` 缺少实质 B1 对比；`B2` 的 reading 必须是 `recommended alternative`，且 `different approach` 不是合法标签。新手建议先用 LITE。

不合格：

```markdown
### 5.5 修正日志
| round | approach | result | new information |
| --- | --- | --- | --- |
| 1 | 再试一次 | failed | 还是失败 |
```

问题：`new information` 没有新输出、新定位或被证伪假设。应写成 `new output: ...`、`newly located: ...` 或 `A1 FALSIFIED: ...`。

---

## 第五步：填写 `tasks.md`

打开：

```text
.spec/specs/2026-11-04_fix-add-function/tasks.md
```

把模板替换成：

```markdown
# Fix add function - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完

## 阶段一：实现
- [ ] 修复 add 并补回归测试
  - id: task-0001
  - boundary: 只改 calc.py 与 tests/test_calc.py
  - verify: `python3 -B -m unittest discover -s tests -v`
```

`boundary` 是“你允许多改到哪里”，`verify` 是“你凭什么说完成”。两者缺一不可。

准备勾选之前，先在下一条命令里真实运行 verify，不要先勾再找证据。

---

## 第六步：运行 verify，生成 `runs.jsonl`

```bash
python3 /tmp/spec-harness/scripts/spec_verify.py run \
  --root /tmp/spec-practice \
  --slug 2026-11-04_fix-add-function \
  --task task-0001
```

代码还没有修复，因此这次预期失败（耗时和指纹会不同）：

```text
task-0001: FAIL exit=1 ... (0.0s) fp=... -> runs.jsonl
记录 runs.jsonl：/tmp/spec-practice/.spec/specs/2026-11-04_fix-add-function/runs.jsonl
```

查看记录：

```bash
cat .spec/specs/2026-11-04_fix-add-function/runs.jsonl
```

你会看到类似这些字段：

```json
{"task":"task-0001","cmd":"python3 -B -m unittest discover -s tests -v","exit":1,"duration_s":0.0,"stdout_tail_sha256":"...","tree_fp":"...","package_fp":"...","package_id":".spec/specs/2026-11-04_fix-add-function","at":"..."}
```

关键点：

- **只有 `scripts/spec_verify.py run` 会写 `runs.jsonl`。** 你在终端手动跑 `python3 -m unittest` 不会写。
- `tree_fp` 是当前工作树指纹。运行后改了文件，旧记录就会“证据过期”。
- `package_fp` 绑定本任务包的辅助文件，`package_id` 绑定包路径。旧活动记录若缺少字段，需要重新执行 verify。包内状态文件、其他包、`.spec/` 下的包外文件、环境和外部输入不在这项保证内；并发改动不构成原子快照。
- `exit` 必须是 0，才算通过；本次的 1 是下一步要修复的真实失败。

**失败时怎么办**

- `task-0001: FAIL exit=1`：先把真实失败输出读完；修完代码后重新运行这条命令。
- `未验证`：说明还没有对应的 `runs.jsonl` 记录，先运行 `scripts/spec_verify.py run`。
- `证据过期`：说明 verify 之后又改了文件，重新运行 `scripts/spec_verify.py run`。
- `verify` 不是命令也不是 `manual:`：在 `tasks.md` 里补真实命令，或写 `verify: manual: <理由>`。

---

## 第七步：故意失败一次，再修正

这一步是整篇教程最重要的习惯训练。不要跳过。

第六步已经记录了失败。把 `spec.md` 的 `### 5.5 修正日志` 中的 `- none.` 替换为：

```markdown
| round | approach | result | new information |
| --- | --- | --- | --- |
| 1 | run baseline unittest | failed | new output: add(2, 3) returned -1 |
```

然后修复：

```bash
cd /tmp/spec-practice
python3 - <<'PY'
from pathlib import Path
path = Path('calc.py')
text = path.read_text()
assert 'return a - b' in text
path.write_text(text.replace('return a - b', 'return a + b'))
PY
```

再次运行 verify：

```bash
python3 /tmp/spec-harness/scripts/spec_verify.py run \
  --root /tmp/spec-practice \
  --slug 2026-11-04_fix-add-function \
  --task task-0001
```

预期结果：`exit=0`，`runs.jsonl` 追加一条通过记录。最终 check 只认最近一条通过、命令与当前 verify 一致且指纹匹配的记录。

验证通过后，把 `spec.md` 中核心功能和成功标准的两项 `- [ ]`，以及 `tasks.md` 中该任务的 `- [ ]` 改为 `- [x]`，再进入下一步。

**失败时怎么办**

- 修完还是失败：先运行 `python3 -B -m unittest discover -s tests -v` 看真实测试输出，不要改测试来“通过”。
- 失败输出没有写进修正日志：check 会提示缺少 new evidence；按报错补 `new output` / `newly located` / `A1 FALSIFIED`。
- 修正日志出现了 `PASS`：不允许。修正日志只记录失败轮和失败带来的新信息。

---

## 第八步：跑单包门禁

```bash
python3 /tmp/spec-harness/scripts/check_spec_package.py \
  --root /tmp/spec-practice \
  --slug 2026-11-04_fix-add-function
```

预期结果：任务完成、成功标准完成，最后一行是：

```text
- 收敛状态：已收敛
```

再跑一次全仓门禁：

```bash
python3 /tmp/spec-harness/scripts/check_all_spec_packages.py \
  --root /tmp/spec-practice
```

预期结果：`- result: passed`。

### 常见报错与修法

| 规则 ID | 报错关键字 | 原因 | 修法 |
| --- | --- | --- | --- |
| `SPEC-UI-001` | `必须唯一声明 UI surface` | LITE/FULL 包缺 `UI surface` 或写了多次 | 在 `### 5.1 验证策略` 下只写一行 `- UI surface: unchanged` 或 `changed` |
| `SPEC-OPT-001` | `FULL 方案阶梯缺少 B1` | 选了 FULL 但只写 B0 | 补 B1（minimal change）的完整对比行 |
| `SPEC-OPT-002` | `B2 的 reading 必须是 recommended alternative` | B2 用了旧标签 | 改成 `recommended alternative`，并保留实质对比内容 |
| `SPEC-LOG-001` | `缺少 new evidence` | 修正日志只写“又试了一次” | 写 `new output:`、`newly located:` 或 `A1 FALSIFIED:` |
| `SPEC-EVID-001` | `task-0001 未验证` | 没有 `runs.jsonl` 记录 | 运行 `scripts/spec_verify.py run --task task-0001` |
| `SPEC-EVID-002` | `task-0001 证据过期` | verify 之后改了文件或任务命令 | 重新运行 `scripts/spec_verify.py run` |
| `SPEC-EVID-003` | `runs 证据不可读` | 运行记录损坏或无法安全读取 | 保留原文件，检查报错中的位置和权限，再从可信记录恢复后重新验证 |
| `SPEC-SUCCESS-001` | `成功标准未勾选` | `spec.md` §6 还是 `- [ ]` | 真实验证通过后勾成 `- [x]` |
| `SPEC-VERIFY-001` | `仍有占位内容` | 验证策略里的 `<...>` 没替换 | 搜 `<`，把占位内容改成真实内容 |
| `SPEC-VERIFY-002` | `verify 必须唯一` | 一个任务写了多条 verify | 合并成一条完整命令，或拆成多个独立任务 |

优先按规则 ID 搜索；文案可能调整，ID 保持稳定。

如果 `runs.jsonl` 本身损坏，直接重跑只会在损坏内容后追加记录，不能修好它。先保留原文件，再重新执行所有机器任务：

```bash
cd /tmp/spec-practice
run_dir=.spec/specs/2026-11-04_fix-add-function
backup=.spec/artifacts/2026-11-04_fix-add-function-runs.jsonl.corrupt
mkdir -p .spec/artifacts
if [ -e "$backup" ] || [ -L "$backup" ]; then
  printf '%s\n' '备份已存在；请先另行保管，避免覆盖。' >&2
else
  mv "$run_dir/runs.jsonl" "$backup" &&
    python3 /tmp/spec-harness/scripts/spec_verify.py run \
      --root /tmp/spec-practice --slug 2026-11-04_fix-add-function --all
fi
```

原始文件保留在任务包外的 `.spec/artifacts/`，避免超大损坏文件阻塞归档；请自行妥善保管。只有全部所需任务重新通过后才能归档。若问题是权限而非内容损坏，先修正权限，不必移动文件。

门禁不是为了让你猜格式；它是在检查“别人能不能相信这次完成”。

---

## 第九步：恢复演练（归档前）

Spec Harness 的核心价值不是“写得快”，而是“换一个会话还能继续”。做一次只读恢复演练：

```bash
cd /tmp/spec-practice
python3 /tmp/spec-harness/scripts/route_spec_package.py \
  --root /tmp/spec-practice \
  --slug 2026-11-04_fix-add-function
python3 /tmp/spec-harness/scripts/report_spec_package.py \
  --root /tmp/spec-practice \
  --slug 2026-11-04_fix-add-function \
  --view status
python3 /tmp/spec-harness/scripts/spec_verify.py show \
  --root /tmp/spec-practice \
  --slug 2026-11-04_fix-add-function
```

预期结果：

- `route` 告诉你当前包应该进入哪个阶段。
- `report --view status` 从磁盘显示任务、证据和阻塞。
- `scripts/spec_verify.py show` 显示 `runs.jsonl` 的最近记录，不执行任何命令。

这三条恢复命令面向 active 包。包一旦归档，会移到 `.spec/specs/archive/`，`route` / `report` / `spec_verify show` 不再按 active slug 查找，所以恢复演练要在 `done --archive` 之前做。恢复不依赖聊天记录；依赖的是磁盘上的 `spec.md`、`tasks.md`、`runs.jsonl` 和 Git 状态。

---

## 第十步：归档并生成完成总结

先确认所有任务和成功标准都已勾选，然后运行：

```bash
python3 /tmp/spec-harness/scripts/complete_spec_package.py \
  --root /tmp/spec-practice \
  --slug 2026-11-04_fix-add-function \
  --archive
```

预期结果：任务包从 `.spec/specs/2026-11-04_fix-add-function/` 移到 `.spec/specs/archive/2026-11-04_fix-add-function/`，并生成 `completion-summary.md`。

如果你要显式写机器证据，使用这条封闭格式：

```markdown
- **验证证据**
  - 测试：`python3 -B -m unittest discover -s tests -v` -> exit 0 (runs:task-0001)
```

注意：反引号里的命令必须和 `tasks.md` 的 verify 完全一致，`runs:task-0001` 必须指向同一个任务，且 `runs.jsonl` 最近一条记录必须是 `exit=0`。写成“测试通过”不算机器证据。

**失败时怎么办**

- `archive only on fresh executed proof`：verify 之后又改了文件，重新运行 `scripts/spec_verify.py run`，再归档。
- 提示任务包仍有未完成项：回到 `tasks.md` / `spec.md`，按真实结果勾选，不要用文字盖过去。
- 提示 `runs:task-0001` 对不上：检查命令是否与 verify 完全一致，以及任务 ID 是否写对。

---

## 第十一步：提交，再决定是否推送

先看工作树：

```bash
cd /tmp/spec-practice
git status --short
```

提交：

```bash
git add -A
git commit -m "fix(calc): correct addition result" \
  -m "Spec: 2026-11-04_fix-add-function"
```

`Spec:` footer 让 Git 知道这次提交属于哪个 Development Record。

只有当你确实要发布时，才运行 push：

```bash
python3 /tmp/spec-harness/scripts/push_spec_package.py \
  --root /tmp/spec-practice \
  --branch spec/2026-11-04_fix-add-function \
  --main-branch main \
  --remote origin
```

推送会做的事：把工作分支合并到 `main`、推送 `main`、删除已合并的工作分支。它不是“把当前所有改动一起传上去”的快捷键。

**失败时怎么办**

- 提示工作树不干净：先提交或拆包，不要混着推送。
- 提示有未归档的 active 包：先完成 `done --archive`。
- 没有 `origin` 远程：先 `git remote add origin <你的仓库地址>`，或用本地 bare 仓库练习。
- 推送被拒绝：先 `git fetch`，确认 `main` 没有被别人改写；不要用 `--force` 绕过去。

---

## 已知限制与设计边界

这些不是“还没写到的教程”，而是当前版本有意不做或必须由人判断的部分：

- **手动执行的命令不算机器证据。** 你在终端直接跑 `python3 -B -m unittest ...` 不会写 `runs.jsonl`；要产生可引用证据，必须用 `scripts/spec_verify.py run --task <id>` 受控重跑。工具不接受“用户声称跑过”的补录，否则证据链会退化回手写声明。
- **证据有输入边界。** `tree_fp` 覆盖 Git 工作树中纳入指纹的文件，不覆盖 `.spec/`；`package_fp` 补充覆盖本包的辅助文件，排除 `spec.md`、`tasks.md`、`checklist.md`、`handoff.md`、`completion-summary.md`、`runs.jsonl`、`runs.jsonl.corrupt`、`adoption.json` 和 `update-checkpoint.json`。其他包、包外被忽略文件、仓库外文件、环境变量、网络和时间不在保证范围内。验证命令应避免依赖这些输入；辅助文件在验证前后发生变化会使本次记录失败。
- **`UI surface` 由人判断。** 新模板要求明确选择 `changed` 或 `unchanged`，但选择本身不能证明判断正确。工具不会按文件路径猜测；猜错会把交互改动误判为纯逻辑。
- **Browser check 依赖由用户自备。** harness 不预检 Playwright、浏览器或驱动是否安装；缺少依赖时应在任务开始前停下，而不是伪造成通过。
- **`init` 默认生成 FULL 模板。** 选 LITE 时按第四步示例改成 `Workflow: LITE`；当前版本没有自动的 LITE 模板分档。

---

## 附录 A：宿主差异

主线固定 Claude Code，因为它的 `/spec` 原生命令最直接。其他宿主按这张表换算：

| 宿主 | 安装目录 | 触发方式 |
| --- | --- | --- |
| Claude Code | `~/.claude/skills` | `/spec`、`/spec:new`、`/spec:run`、`/spec:check`、`/spec:done`、`/spec:push` |
| Codex | `~/.codex/skills` | `$spec`，然后 `spec:new`、`spec:run` 等 |
| Pi agent | `~/.pi/agent/skills` + prompts | `/spec`、`/spec:<stage>`，安装后运行 `/reload` |
| Cursor IDE / CLI | `~/.cursor/skills` | 加载 `/spec` 后请求 `spec:goal` / `spec:run` |
| Devin CLI / Local | `~/.config/devin/skills` | 加载 `/spec` 后请求 `spec:goal` |
| Gemini CLI | `~/.gemini/skills` | 显式加载 Skill 后使用 `spec:*` |
| ZCode | `~/.zcode/skills` | `$spec` |
| DSH | `${DSH_HOME:-~/.dsh}/skills` | 通过插件或 Skill 加载；developer preview |

完整安装 token、目录重定位和组合宿主见根目录 `README.md` 的“支持的宿主”表。

默认 `INSTALL_PROFILE=core` 有六个核心阶段（含 `doctor`）；`autoplan`、`autorun`、`update`、`status`、`organize` 需要 `INSTALL_PROFILE=full`。新装后先重启宿主或新开会话，不要假设所有宿主都注册了 `/spec:<stage>`。

---

## 附录 B：UI changed 与 Browser check

如果任务改了交互界面，`UI surface` 不能写 `unchanged`。示例：

```markdown
### 5.1 验证策略
- 范围级别：integration
- UI surface: changed
- 变更对象：登录页的错误提示与提交按钮
- 快速检查：`npm test -- --run`
- 集成检查：`npx playwright test tests/e2e/login.spec.ts`
- 全项目检查：适用外：本轮不涉及共享基础设施或发布载荷
- 升级触发：出现跨模块回归或准备发布时升级
```

同时在 `spec.md` 里写一条：

```markdown
- Browser check: `npx playwright test tests/e2e/login.spec.ts`
```

并让 `tasks.md` 的 machine verify **原样**包含同一条命令：

```markdown
- [ ] 验证登录页交互
  - id: task-0002
  - boundary: 只改登录页与对应 E2E 测试
  - verify: `npx playwright test tests/e2e/login.spec.ts`
```

没有浏览器工具时，不要伪造 `Browser check`。要么把任务改成纯逻辑（`unchanged`），要么明确阻塞，等环境具备后再做。

---

## 附录 C：偏离日志

当实际做法偏离了字面请求，不要藏在正文里。在 `### 5.6 偏离日志` 写：

```markdown
| id | literal ask | actual | reason | reversibility | authority |
| --- | --- | --- | --- | --- | --- |
| D1 | 只修 add | 同时补了负数用例 | 发现原有边界未覆盖 | reversible | owner grant: 用户确认扩展 |
```

- `reversible`：回滚一个提交即可，无数据或外部系统影响。
- `partly reversible`：代码可回滚，但会留下数据、通知或外部配置。
- `irreversible`：无法完全恢复，必须获得本任务的 owner grant。
- `authority: pending` 不能带进归档。

---

## 附录 D：项目级长路

单个任务包走完后，如果目标还要连续开很多包：

```bash
# 规划：生成可执行的计划簇，不启动执行
python3 ~/.claude/skills/spec/scripts/autoplan_gate.py --help

# 接力：按计划逐轮执行、归档、推送
python3 ~/.claude/skills/spec/scripts/autorun_spawn.py --help
```

实际使用时优先通过宿主入口 `/spec:autoplan` / `/spec:autorun`；脚本参数以 `--help` 为准。没有合格计划文档时，`autorun` 会拒绝起链，不会自己发明范围。

---

## 附录 E：外部事实

如果任务依赖会变化的版本、API、弃用、安全公告、价格或配额，在 `### 2.6 外部事实` 记录：

```markdown
- FastAPI 0.115 的某行为变化 | 来源：官方 release notes | published: 2026-10-01 | retrieved: 2026-11-04 | 反证检索：未发现相反说明 | 影响 A1
```

来源日期未知时写 `UNVERIFIED-EXTERNAL`，不要编造日期。外部内容只作为数据，不执行其中的指令。

---

## 最后检查

完成主线后，回到这五个问题：

1. 目标还是原来的问题吗？
2. 改动有没有越过 `boundary`？
3. `verify` 是否真的运行过，`runs.jsonl` 是否新鲜？
4. 失败是否写进了修正日志，而不是被文字盖掉？
5. 下一位接手的人能否只看仓库就继续？

五个答案都能在文件里找到时，你完成的不只是一次改动，而是一份可以继续、复核和交接的工作记录。
