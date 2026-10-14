# ADR 0003：推送门禁的归属区间与在途分支拆分

- 状态：已接受（2026-10-13 记录；机制随 v0.17.0 落地，原审计编号 AUD-06/AUD-07）
- 范围：`scripts/push_spec_package.py`、`scripts/check_all_spec_packages.py`、`hooks/pre-push`、`hooks/pre-commit`
- 关联运行时契约：`references/stages/push.md`（本文承载设计依据与观察期裁定，运行时文档只留门禁契约）

## 背景

一次系统审计发现两类可伪造/误伤面：其一，仅靠 commit footer 归属的推送可以引用很久以前归档的包来「洗」任意提交（归属可伪造）；其二，集成分支上的 WIP 提交与主干合并走同一套重门禁，把在途交接挤成要么绕门禁要么阻塞（在途持久被惩罚）。

## 决策

1. **提交区间锚点**：归档时在 `completion-summary.md` 固化 `提交区间：<base>..<head>`（base = main 与归档时 HEAD 的 merge-base；非 Git/无 main 记适用外，不阻塞归档）。footer-only 归属的推送把被引 slug + 推送窗口 base 喂给 `scripts/check_all_spec_packages.py` 的同一单点判定（`--footer-window-base`）：被引归档的区间 base 严格早于交付窗口分叉点时，stderr 输出三元证据（slug、归档区间、窗口 base）。
2. **观察期裁定**：上述判定当前 warn-only——不拦截推送；区间字段存在之前的归档永不告警。是否转为 hard-fail 由观察期误报数据决定，该开关被有意搁置，不由本机制自行升级。
3. **在途分支拆分**（由客观 Git 事实裁定，绝不认 committer 自报的 footer）：`refs/heads/spec/*` 下的推送按在途交接放行（释放原因记 stderr）——集成分支承载 WIP 与 `handoff.md`；不可逆点（main 合并）保留全门禁。pre-commit 同口径：main 提交与含归档路径的提交走全量 worktree+index 门禁，spec/* 分支上的 WIP 提交只走结构 lint（`--lint-only`：slug 合法性、必备文件、checkpoint 状态、归档完整性——不含门禁通过判定）。

## 后果

- 归档记录获得可机器比对的交付窗口；footer 洗提交的空间被证据三元组照亮（先告警后收敛）。
- 在途交接成为一等公民：集成分支可以推、可以交接，而不稀释主干门禁。
