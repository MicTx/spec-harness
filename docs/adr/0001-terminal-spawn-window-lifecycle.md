# ADR 0001：无人值守链的会话生成与 Terminal 窗口生命周期

- 状态：已接受（2026-10-13 记录；机制自 2026-10-06 起演进）
- 范围：`scripts/autorun_spawn.py`、`scripts/autoplan_spawn.py`、`scripts/sweep_chain_windows.py`
- 关联运行时契约：`references/stages/autorun.md`、`references/stages/autoplan.md`（本文件承载设计依据与政策沿革，运行时文档只留契约）

## 背景

autorun 每轮、autoplan 每个 pass 都需要一个全新的完整交互会话（可见 TUI、会话持久化，绝不以 headless print/exec 模式偷跑）。macOS 上这个需求落在 Terminal.app + osascript；Linux/WSL2 落在私有 tmux server。窗口/会话的打开、确认、回收涉及 AppleScript 自动化与进程生命周期管理，细节多次演进，故把「为什么这样设计」与沿革集中于此。

## 决策

1. **自动化域三步授权**：Terminal.app/osascript 自动化只允许出现在三处——autorun 轮生成、autoplan pass 生成、死链窗口清扫（`scripts/sweep_chain_windows.py`）。其余任何阶段不得操纵 Terminal 或 AppleScript。该授权推翻了 2026-09-03 的「禁止 AppleScript」政策， overturn 记录在 `2026-10-06_add-autorun-command` 开发记录；autoplan 扩展记录于 `2026-10-07_add-autoplan-pass-chain`；清扫扩展记录于 `2026-10-08_sweep-dead-chain-windows`（历史归档，不改写）。
2. **交接未确认 = 不留进程**（2026-10-09 裁定）：从「窗口已开」到「链状态已写入」之间的任何失败，都必须同步关掉刚开的窗口——先自然退出宽限，再 SIGTERM，再 SIGKILL（约 30 秒有界，记一条 `kind=refusal_close` 事件）；识别不了窗口就如实记跳过原因，绝不强关用户窗口。状态写入即交接确认，其后的失败（如审计追加失败）绝不再关该窗口——它已是链的合法 worker。
3. **启动门（start gate）**：瞬间死掉的 worker（codex 的概率性启动失败）被捕获——静默关闭死窗口、零状态写入地拒绝 spawn（exit 1 + `kind=spawn_refusal`），重跑从原点恢复；「忙但未观测到」的 tab 按迟到启动处理，放行。
4. **窗口回收（recycling）**：下一轮 worker 在新窗口 tty 上被确认运行后（有界 60 秒轮询；`--command` 原始覆盖可信、免轮询），前一窗口经 detached helper 延迟数秒关闭，让本轮总结先落地、长链不再每轮叠一个窗口。helper 升级路径：自然退出宽限 → SIGTERM 进程组 → SIGKILL → 关闭空闲窗口——交互式 TUI 完成回合后永久闲置正是被回收的对象，进程先消失所以 cancel/terminate 面板不会出现。回收 fail-open：无 Terminal.app 控制窗口、worker 未按时观测到、窗口共享多 tab、会话仍在运行——一律跳过并记录原因，窗口保持打开；绝不在未确认交接时关窗，绝不对运行中的会话弹出取消确认框。
5. **几何继承**：新窗口继承生成会话的位置与尺寸，递归链原地堆叠而不是级联铺屏。
6. **tmux 后端**（Linux/SSH/WSL2）：私有、无配置 tmux server（`tmux -L …`），同样的完整交互 worker；失败时识别新 pane 的属主、验证其独立 POSIX 进程组/会话，TERM→KILL 有界退出检查后才关 session（`kill-session` 本身不是 worker 终止证明——worker 可忽略 HUP）；tmux 从不关生成 shell/用户窗口/前一会话，回收记为跳过。原生 Windows 不直接支持 spawn，给出 WSL2 指引而非 `fcntl`/`osascript` 回溯。
7. **`--dry-run`**：只打印 `launch_argv` 与 attach 指引，不启动会话、不写链状态。

## 沿革注记

轮上限/权限模式/特性指纹的恢复（链安全轨）设计记录在 ADR 0002 之前的开发记录与 CHANGELOG（v0.17.0 区段）；本文只钉会话与窗口生命周期。
