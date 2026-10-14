# ADR 0002：阶段契约按文件拆分（references/commands.md → stages/）

- 状态：已接受（2026-10-13，集群 F13 / AUD-15）
- 范围：`references/commands.md`、`references/stages/`、消费它的契约测试
- 关联：`docs/adr/0001-terminal-spawn-window-lifecycle.md`（承接自 autorun/autoplan 的叙事外移）

## 背景

`references/commands.md` 曾是 89KB 单文件、14 节（最大单节 17.7KB）。按阶段读契约的场景被单文件形态惩罚：模型要么整读（上下文浪费），要么按节切片（锚点脆弱）。文件同时积压实现叙事与历史引用（F 编号沿革、带日期政策裁定、失败案例长例），运行时注意力被历史占据。

## 决策

1. **一阶段一文件**：`references/stages/<stage>.md` 承接对应阶段的完整运行契约；文件名逐一取自 `install.sh` 的 `USER_STAGES`（工程真源，`tests/test_stage_inventory.py` 钉住），不发明第二套阶段表。`/spec` 裸入口、CLI compatibility layer、Script working-directory conventions 属跨阶段共享面，留守索引页。
2. **字节预算**：每 stage 文件 ≤5,120 B，由守护测试钉住；超限内容属叙事残留，继续外移而不是放宽上限。
3. **references/commands.md 降为薄索引**：每阶段一节一句目的 + 链接；`## `/spec:<stage>`` 标题形态保留（stage inventory 正则依赖）；F4 的语义真源职责移交 stages/，安装对照锚点测试的真源指针随切，锚点断言词不变。
4. **历史二分去向**：跨版本行为沿革 → `CHANGELOG.md`；单点架构裁定 → `docs/adr/`。运行时文档（SKILL.md、references/ 树）清零 F 编号与带日期政策引用；语义性日期（如 grace window 起算日）不算历史引用。历史归档与 `.spec/` 永不改写。
5. **ADR 以纯路径文本被引用**：`docs/` 不进运行时导出树（COPY_DIRS 不含 docs），stages/ 内不写指向 docs/ 的 markdown 链接（安装副本会断链、链接解析守护会红），只写「源码仓库 `docs/adr/…`」路径文本。
6. **已知缺口**：`scripts/doctor_spec_environment.py` 的 `REQUIRED_RUNTIME_FILES` 清单本轮不扩展（scripts/ 不在本包改动面）；stages/ 文件由目录复制随载荷分发但不被 doctor 布局检查覆盖，留给安装面收敛轮（集群 F16）处置。

## 后果

- 消费方（模型、测试、安装器 heredoc 对照）从「读大文件切片」改为「直读小文件」；锚点测试逐文件指认，失败定位从节级到文件级。
- 公开导出树新增 `references/stages/` 子树；README 双页导出树由导出器对照测试强制同步。
- `git grep 'F1[0-9]' SKILL.md references/` 为零成为守护项（历史归档除外）。
