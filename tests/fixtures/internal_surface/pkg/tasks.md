# 合成包 - 任务拆解

## 使用规则
- 合成夹具：boundary/verify 文本只供解析，从不执行

## 阶段一：混合状态
- [ ] 首个任务
  - boundary: 合成边界一
  - verify: `python3 -m pytest -q tests/`
- [x] 已完成任务
  - boundary: 合成边界二
  - verify: `python3 -m pytest -q tests/`
- [!] 受阻任务
  - blocked: 等待合成授权
  - boundary: 合成边界三
  - verify: `python3 -m pytest -q tests/`
- [ ] 依赖任务
  - depends-on: task_0001
  - boundary: 合成边界四
  - verify: `python3 -m pytest -q tests/`
- [>] 手工进行中任务
  - boundary: 合成边界五
  - verify: `python3 -m pytest -q tests/`

---

**当前进度**：由脚本计算，无需手动维护
