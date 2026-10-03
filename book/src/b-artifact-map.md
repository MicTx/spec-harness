# 附录 B 制品与目录地图

恢复任务时，最先遇到的不是命令，而是一堆文件。本附录按“谁维护什么事实”整理目录，让查找顺序先于文件数量。

## 场景：项目有很多文件，却没有明确的记忆

一个目录里堆满会议摘要、聊天导出、模型计划、工作日志和测试截图，并不意味着这个项目具有可恢复性。新会话进入之后，仍然可能不知道该读哪个文件。文件数量与项目记忆质量没有必然关系；如果两份计划都自称最新，它们甚至比没有计划更危险。

陈默在整理照片迁移任务时，把目录分成三个问题：哪些文件定义当前工作，哪些文件只是过去工作的证据，哪些文件属于可以清理的临时协调状态。这个分类看似朴素，却决定了恢复过程是查表还是考古。

本附录是一张职责地图。重点不是把所有路径记下来，而是认识文件所承担的语义。换一个 Harness 实现，路径可能变，三种职责仍然存在。尤其要警惕把展示文件当成真源：一个漂亮的看板可以从任务包生成，但不能反过来成为第二套进度记录。

## 原则：同一真源不是一个文件装下一切

同一真源说的是同一事实只有一个权威维护位置，不是整个系统只能有一个文件。范围写在范围文件，任务状态写在任务文件，验收结论写在验收文件；三者不互相复制同一个字段，才构成一致的任务包。把所有内容堆进一篇长文，不会自动得到一致性，反而会失去局部解析与局部更新的能力。

证据文件也有独立价值。测试输出、制品摘要、构建记录解释某次结论从何而来，但不应该靠一句「PASS」自行改变任务状态。主线程读过证据、核对对象与范围之后，才更新任务包。换言之，证据支持决定，不替代授权。

临时状态应被隔离，而不是被假装不存在。助手需要租约、心跳、输入卡片、结果卡片，这些都是真实运行状态；但它们不应让 Git 历史充满每秒刷新的噪声。把它们放进忽略目录，并不等于里面可以存放长期唯一成果。助手真正的交付必须被验证、合入正式路径，才成为版本库的一部分。

## 最小实现：从三份文件到一次可交接交付

### 教学目录

本书的教学程序采用单独目录名，刻意不伪装成参考实现的完整兼容版本：

```text
练习仓库/
├── .mini-harness/
│   └── photo-migration/
│       ├── spec.md
│       ├── tasks.md
│       ├── checklist.md
│       └── evidence.json
├── app/
│   └── storage.py
└── tests/
    └── test_storage.py
```

前三个文件定义工作，最后一个 JSON 由最终验证命令生成。JSON 内的内容摘要用于核对验证时看到了哪三份文档，不是对全部源代码的自动证明。源代码新鲜度仍要结合 Git diff、提交标识或额外的构建输入摘要核对。这一限制必须明说，否则读者容易把「文档未变」误读成「代码未变」。

| 文件 | 回答的问题 | 谁维护 | 什么时候读取 |
| --- | --- | --- | --- |
| spec.md | 要解决什么，不解决什么 | 主线程与需求负责人 | 每次恢复、范围改变、验收前 |
| tasks.md | 当前有哪些任务，什么依赖什么 | 主线程合流后更新 | 每步执行前后 |
| checklist.md | 是否满足验收，证据在哪里 | 最终审核者 | 检查、归档前 |
| evidence.json | 哪个命令在哪次快照上运行 | 程序记录，人工审查 | 验证后与交接时 |
| app 与 tests | 实际交付及行为断言 | 有授权的实现者 | 实现、测试、审查 |

教学版的证据文件会由下一次验证原子替换，便于观察最近一次结果，但这不是审计保留策略。需要完整历史时，应按运行标识保存不可变证据，并设保留期限。没有这个需要时，不必为了仿照大型平台而建立日志数据库；有这个需要时，也不要把覆盖式文件说成完整审计系统。

### 参考实现的目录

```text
参考仓库/
├── .spec/
│   ├── specs/
│   │   ├── YYYY-MM-DD_verb-object/
│   │   │   ├── spec.md
│   │   │   ├── tasks.md
│   │   │   └── checklist.md
│   │   └── archive/
│   └── docs/
├── .agents/
│   └── runtime/
│       └── helpers/
└── scripts/
```

这是职责示意，不是要求你的仓库逐字复制。参考实现的归档脚本还可能生成完成摘要、知识沉淀等文件；它们的确切形态以该版本实现为准。阅读时要区分两类变化：新任务包是一次新工作，归档是旧任务包完成后的存放位置变化。归档不应制造第二份仍然活动的任务包，否则路由会遇到重复身份。

`spec.md` 记录集成分支绑定，使工具能从当前工作分支找到当前包。多个包并存时，这比「选择最后修改的目录」可靠。分支不是任务内容本身，但它把工作流状态和 Git 的改动集合关联起来。绑定缺失、重复或与现场不符，都应该作为需要处理的状态，而不是由看板悄悄猜测。

`.agents/runtime/helpers/` 承担临时助手协调。输入卡片说明助手获批读取什么，任务卡片说明它负责什么，结果卡片记录它提交什么，租约与 fencing 信息约束何时仍具有协作资格。它们不能让主线程免于独立检查，也不能阻止同一操作系统用户下的恶意进程越权访问其他文件。权限隔离若属于需求，必须由另外的执行机制提供。

### 本书自己的制品

本书把可编辑章节与组装稿分开存放，原因不是喜欢目录，而是并行写作需要清晰的写所有权。三个人同时改一个巨大的 `book.md` 会频繁碰撞；各自写互斥章节，主线程统一组装，冲突少得多。

```text
book/
├── src/
│   ├── outline.md
│   ├── glossary.md
│   ├── 00-preface.md
│   ├── 01-agent-out-of-control.md
│   ├── 03-four-principles.md
│   ├── 04-task-package.md
│   ├── 05-single-loop.md
│   ├── 06-machine-gates.md
│   ├── 07-git-landing.md
│   ├── a-command-map.md
│   ├── b-artifact-map.md
│   ├── c-failure-modes.md
│   └── book.md
├── examples/
│   ├── mini_harness.py
│   └── test_mini_harness.py
├── metadata.yaml
├── verify_ebook.py
└── dist/
    └── from-zero-agent-harness.epub
```

章节文件是编辑真源，组装稿与 EPUB 是派生产物。修改文章应该回到章节，而不是直接修生成的 EPUB。否则下一次构建就会覆盖修改，造成「昨天已经改过，今天怎么又回来了」的困惑。派生产物可以入库也可以不入库，取决于分发方式；无论如何，重建路径必须明确。

`outline.md` 与 `glossary.md` 分别控制阅读顺序与术语。它们不应该被写作助手随意修改，因为改变这两份文件会影响每一章。它们是典型共享边界，应由主线程维护。各章的写作合同可以引用它们，但不因此获得改写权。

`metadata.yaml` 提供书名、语言、作者标识与描述。元数据属于出版内容，不应虚构发行成绩、认证或背书。`verify_ebook.py` 可以检查文件、标题、字数和构建结果，但不能替编辑确认论证质量。这就是为什么目录地图必须同时说明文件职责和验证边界。

### 交接包的最小内容

一次有效交接不需要把整段对话打包。它需要：任务包路径、集成分支或当前工作区位置、未完成任务、已验证命令、产物位置、明确的阻塞与禁止动作。接手者先复查现场，再决定是否继续执行；不要要求他无条件信任前一个会话的文字摘要。

对照片迁移任务，交接还需要数据侧状态：已迁移对象范围、对账结果、幂等键规则、失败队列位置。代码状态与外部状态不同步时，单独一个 Git 提交无法恢复全部事实。仓库能保存的是查询外部状态的办法与上次查询证据，而不是自动替外部服务保持一致。

对写书任务，交接需要已完成章节、术语约定、字数统计、未通过的检查、构建工具版本。没有外部数据库，也仍有环境依赖：机器上没有 Pandoc 时，Markdown 完整不意味着 EPUB 已生成。把这种依赖写清，比报一句「剩下只是打包」更有帮助。

## 反模式：五种目录上的自欺

第一种是双写真源。把任务进度同时写在 `tasks.md`、电子表格和看板文件中，却没有规定哪个字段由谁生成。只要一次更新漏掉一处，三个位置就会给出三个答案。解决方法是保留一个权威写入点，其余视图只读生成。

第二种是把临时工作区当交付。助手说文件写好了，主线程只保存它的路径，没有导入和检查。临时目录被回收后，唯一成果消失。交付必须经过正式路径合流，不能寄托在运行期缓存永远不清理的愿望上。

第三种是误清理。看到 `.agents` 就全删，以为都是无用数据；或者看到未跟踪文件就一并清空。临时目录也可能含尚未消费的结果。先查运行生命周期、确认无活动任务，再执行限定清理，不用宽泛通配符替代判断。

第四种是修改生成物。直接改 `book.md` 或电子书压缩包，不回写章节源稿。短期看问题消失，长期看构建不再可重放。正确修复位置是产生错误的最上游权威输入。

第五种是把所有东西纳入版本控制。高频心跳、临时日志、秘密凭证与正式任务文档混在一起，既污染历史又增加泄露风险。文件应不应该提交，取决于职责、敏感性和可重建性，不取决于它是不是刚被某个 Agent 创建。

## 练习：给每个文件标明主人

在你的仓库中挑十个与智能体工作相关的文件，分别标注「定义工作」「支持证据」「临时协调」「派生产物」。有些文件可能混合两种职责，这正是值得拆分或明确规则的地方。验收判据：每个事实都有唯一的权威维护位置，每个派生产物都能指出自己的生成输入。

随后做一次目录丢失演练，但只在练习仓库的复制品里进行。删除生成稿，尝试从章节重建；删除临时运行状态，确认正式交付仍可读取；保留任务包却删除聊天记录，观察你能否继续工作。如果某次删除导致关键事实永久消失，把那项事实迁到恰当的持久位置，再重做实验。

最后写一份不超过十行的交接说明。让接手者只凭说明找到当前任务、运行检查并识别一个故意留下的未完成项。能找到不是终点，能够不误判为完成，才是这份目录地图真正发挥作用的证据。

## 版本与参考资料

本书的参考对象是当前 Spec Harness 仓库。恢复时的基线提交为 `7eccc4030e891379db0786ec632f44e0c561f674`；同一工作区另有未提交 HUD 改动，本书不把它们纳入自己的运行时修改。核心行为依据根目录 `SKILL.md`、`references/commands.md`、`references/orchestration.md` 及对应脚本阅读，不以销售资料充当技术规范。

进一步核对接口时，可查阅以下原始资料；它们是规范和工具文档，不是对本书质量的背书：

- Python 标准库文档：subprocess 的参数数组、超时与进程行为，`https://docs.python.org/3/library/subprocess.html`。
- Git 官方文档：工作区与 worktree 的隔离方式，`https://git-scm.com/docs/git-worktree`。
- Pandoc 用户手册：EPUB 输出、元数据、目录与样式选项，`https://pandoc.org/MANUAL.html`。
- W3C EPUB 3.3 规范：出版物资源、封装和阅读顺序，`https://www.w3.org/TR/epub-33/`。

工具接口随版本演进，本书提供可复现的教学实现及实测记录，不保证未来所有版本参数不变。引用线上资料时，应核对正在使用的工具版本；引用本书测试时，应核对测试所覆盖的平台与对象。

## 教学程序完整源码

以下源码与随书的 `examples/mini_harness.py` 一致。电子书独立阅读时，可保存为该文件运行；需要完整测试时使用随书源代码目录。本节是协作式教学控制层，能力边界见第 8 章与示例说明，不包含模型调用或生产存储业务。

```python
#!/usr/bin/env python3
"""A single-writer teaching harness. Commands never execute Markdown as code."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

FILES = ("spec.md", "tasks.md", "checklist.md")
LIMIT = 1024 * 1024
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
ITEM = re.compile(r"^- \[([ x])\] (\S.*)$")
FIELD = re.compile(r"^  - (id|depends-on|boundary|verify|evidence): (\S.*)$")


class Invalid(ValueError):
    pass


def package(root: Path, slug: str) -> Path:
    if not SLUG.fullmatch(slug):
        raise Invalid("slug must be lowercase words separated by hyphens")
    base = root / ".mini-harness"
    path = base / slug
    if base.is_symlink() or path.is_symlink():
        raise Invalid("package paths must not be symlinks")
    return path


def documents(path: Path) -> dict[str, str]:
    result = {}
    for name in FILES:
        p = path / name
        if p.is_symlink() or not p.is_file():
            raise Invalid(f"missing regular file: {name}")
        if p.stat().st_size > LIMIT:
            raise Invalid(f"file exceeds 1 MiB: {name}")
        result[name] = p.read_text(encoding="utf-8")
    return result


def parse_tasks(text: str) -> list[dict]:
    tasks: list[dict] = []
    for line in text.splitlines():
        match = ITEM.fullmatch(line)
        if match:
            tasks.append({"done": match[1] == "x", "title": match[2]})
            continue
        field = FIELD.fullmatch(line)
        if field:
            if not tasks or field[1] in tasks[-1]:
                raise Invalid("orphan or duplicate task field")
            tasks[-1][field[1]] = field[2]
        elif line.startswith("  - ") or line.startswith("- ["):
            raise Invalid(f"malformed task line: {line}")
        elif line.strip() and not line.startswith("#"):
            raise Invalid("tasks.md accepts headings and task records only")
    if not tasks:
        raise Invalid("tasks.md has no tasks")
    ids = [t.get("id", "") for t in tasks]
    if len(set(ids)) != len(ids) or any(not SLUG.fullmatch(i) for i in ids):
        raise Invalid("task ids must be valid and unique")
    by_id = dict(zip(ids, tasks))
    for t in tasks:
        if not t.get("boundary") or not t.get("verify"):
            raise Invalid(f"{t['id']}: boundary and verify required")
        t["deps"] = [s.strip() for s in t.get("depends-on", "").split(",") if s.strip()]
        if len(set(t["deps"])) != len(t["deps"]) or any(d not in by_id for d in t["deps"]):
            raise Invalid(f"{t['id']}: duplicate or missing dependency")
    # Iterative topological validation avoids recursion limits on long chains.
    resolved: set[str] = set()
    while len(resolved) < len(tasks):
        batch = {t["id"] for t in tasks if t["id"] not in resolved and set(t["deps"]) <= resolved}
        if not batch:
            raise Invalid("task dependency cycle")
        resolved.update(batch)
    for t in tasks:
        if t["done"] and any(not by_id[d]["done"] for d in t["deps"]):
            raise Invalid(f"{t['id']}: completed before its dependency")
        if t["done"] and not t.get("evidence"):
            raise Invalid(f"{t['id']}: completed without evidence")
    return tasks


def single_field(text: str, name: str) -> str:
    values = re.findall(rf"^{re.escape(name)}: (.+)$", text, re.M)
    if len(values) != 1:
        raise Invalid(f"exactly one {name} field required")
    return values[0].strip()


def state(docs: dict[str, str]) -> tuple[str, list[dict]]:
    status = single_field(docs["spec.md"], "Status")
    if status not in {"draft", "active"}:
        raise Invalid("Status must be draft or active")
    tasks = parse_tasks(docs["tasks.md"])
    checklist = docs["checklist.md"]
    acceptance = single_field(checklist, "Acceptance")
    if acceptance not in {"pending", "passed"}:
        raise Invalid("Acceptance must be pending or passed")
    checks = []
    for line in checklist.splitlines():
        match = ITEM.fullmatch(line)
        if match:
            checks.append(match[1] == "x")
        elif line.startswith("- ["):
            raise Invalid("malformed checklist item")
    if not checks:
        raise Invalid("checklist must contain checks")
    complete = all(t["done"] for t in tasks)
    if acceptance == "passed" and (status != "active" or not complete or not all(checks)):
        raise Invalid("acceptance contradicts task or checklist state")
    if status == "draft":
        return "plan", tasks
    if not complete:
        return "execute", tasks
    if acceptance != "passed" or not all(checks):
        return "review", tasks
    if not single_field(checklist, "Evidence"):
        raise Invalid("acceptance evidence is empty")
    return "ready-to-archive", tasks


def hashes(docs: dict[str, str]) -> dict[str, str]:
    return {n: hashlib.sha256(t.encode()).hexdigest() for n, t in docs.items()}


def init(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()  # Never overwrite an existing package.
    content = {
        "spec.md": (
            "# Photo migration\n\n"
            "Status: draft\n\n"
            "## Goal\n"
            "Move storage behind an adapter, preserving API behavior.\n\n"
            "## Non-goals\n"
            "No cache refactor or production data deletion.\n"
        ),
        "tasks.md": (
            "# Tasks\n\n"
            "- [ ] Implement storage adapter\n"
            "  - id: adapter\n"
            "  - boundary: app/storage.py, tests/test_storage.py\n"
            "  - verify: python3 -m unittest discover -s tests\n"
        ),
        "checklist.md": (
            "# Acceptance\n\n"
            "- [ ] Tests cover failure and retry\n"
            "- [ ] Diff stays inside approved boundary\n\n"
            "Acceptance: pending\n"
        ),
    }
    for name, text in content.items():
        (path / name).write_text(text, encoding="utf-8")
    print(f"created {path}; edit draft before execution")


def verify(path: Path, root: Path, command: list[str], timeout: int) -> int:
    if not command:
        raise Invalid("verify requires an operator-approved argv after --")
    before = documents(path)
    if state(before)[0] != "ready-to-archive":
        raise Invalid("finish structural check before final verification")
    # Output goes to temporary files, not an unbounded in-memory pipe.
    # This demo has no disk quota; run noisy tools in a quota-limited workspace.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(command, cwd=root, stdout=out, stderr=err, start_new_session=(os.name == "posix"))
        timed_out = False
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait()
            code = 124
        except KeyboardInterrupt:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait()
            raise
        summaries = {}
        for name, stream in (("stdout", out), ("stderr", err)):
            size = stream.tell()
            stream.seek(0)
            summaries[name] = {
                "bytes": size,
                "truncated": size > 65536,
                "text": stream.read(65536).decode("utf-8", errors="replace"),
            }
    unchanged = hashes(before) == hashes(documents(path))
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "argv": command,
        "cwd": str(root),
        "returncode": code,
        "timeout": timed_out,
        "documents": hashes(before),
        "documentsUnchanged": unchanged,
        **summaries,
    }
    # Atomic replace prevents a torn evidence file, but is not a multi-writer lock.
    fd, temporary = tempfile.mkstemp(dir=path, prefix=".evidence-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path / "evidence.json")
    finally:
        Path(temporary).unlink(missing_ok=True)
    print(f"verify returncode={code} documentsUnchanged={unchanged}; {path / 'evidence.json'}")
    return 0 if code == 0 and unchanged else 1


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    command: list[str] = []
    if "--" in raw:
        index = raw.index("--")
        raw, command = raw[:index], raw[index + 1 :]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "route", "check", "verify"))
    parser.add_argument("slug")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args(raw)
    if not 1 <= args.timeout <= 3600:
        parser.error("timeout must be 1..3600 seconds")
    if args.action != "verify" and command:
        parser.error("only verify accepts a command")
    try:
        root = Path.cwd().resolve()
        path = package(root, args.slug)
        if args.action == "init":
            init(path)
            return 0
        if args.action == "verify":
            return verify(path, root, command, args.timeout)
        stage, tasks = state(documents(path))
        print(stage)
        if args.action == "check":
            return 0 if stage == "ready-to-archive" else 1
        if stage == "execute":
            done = {t["id"] for t in tasks if t["done"]}
            for t in tasks:
                if not t["done"] and set(t["deps"]) <= done:
                    print(
                        f"{t['id']}: {t['title']}\n  boundary: {t['boundary']}\n  verify (not executed): {t['verify']}"
                    )
        return 0
    except KeyboardInterrupt:
        print("cancelled; no successful verification recorded", file=sys.stderr)
        return 130
    except (OSError, UnicodeError, Invalid) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
```
