# 合成示例包（v2）- 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`

## 阶段一：合成实现
- [ ] 合成任务 A
  - boundary: 仅合成文件 A
  - verify: `python3 -c "print('fixture-a')"`
- [ ] 合成任务 B
  - depends-on: task_0001
  - boundary: 仅合成文件 B
  - verify: `python3 -c "print('fixture-b')"`

---

**当前进度**：由脚本计算，无需手动维护
