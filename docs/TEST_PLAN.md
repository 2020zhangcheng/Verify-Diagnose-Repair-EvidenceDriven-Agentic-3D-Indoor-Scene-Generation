# 测试与实验合同

V0 已运行合同测试及真实 PostgreSQL 集成测试；最新验收见 V0_ACCEPTANCE.md。未来验收必须分清 schema、服务语义、数据库集成与研究有效性，schema 通过不表示 Agent 可运行。

## 当前合同测试

非法单位四元数、概率越界、NaN/Inf、未知字段、无时区时间均拒绝；SceneBelief/Observation/Event JSON 往返无信息丢失；部分观测可以表示未观测、几何缺失与冲突；CriticalUnknown 至少两个 outcome，view 关联未知；8 个端口与状态可导入。测试不实现规划或评分。

## 后续工程验收

| 范围 | 核心场景 | 通过条件 |
| --- | --- | --- |
| Event/DB | 每个 SQL 边界故障注入、重复 key、并发 task 序号 | 无事件丢失、无半投影、同 key 不同 body 拒绝，UPDATE/DELETE 被禁 |
| Agent/checkpoint | 任一 node 前后 crash、结果提交与 checkpoint 间 crash | 从持久 saver 恢复，重用 operation 结果，无重复动作 |
| Runner | 两个 worker 争抢 task、lease 过期旧 worker 回来 | 单一有效 owner，fencing 拒绝旧 writer |
| Memory | debounce、新事件遇 running batch、lease 超时、Mem0 成功后崩溃 | 不吞新事件，有界重试、对账去重、scope 隔离 |
| Belief | 遮挡物、多视角误差、无 depth、重放同 observation | 未知不变 free，冲突显式上升，版本和证据不重复 |
| Planner/Unknown | 窗下暖气 exists/not_exists；与任务无关的高不确定区域 | ≥2 个真实候选；关键 outcome 改变可行性/排序，无关未知不抢占决策 |
| View | 小视野看清暖气 vs 大视野无关；不可达/无收益/预算耗尽 | 选择任务关键视角；无有效视角安全停止，不捏造分数 |
| Geometry | 碰撞、墙重叠、门开启区、通道、房内、最小间距、可见性、距离 | 确定性结果，边界容差固定，缺失输入 unknown |
| Execution | belief/layout/env 版本变更、规则缺项、unknown/fail、partial action | 阻止无效执行；unknown 必须对账；post validate 后才完成 |
| API/Trace | 越权、分页版本、重复 run、缺失证据 | 范围隔离、分页稳定、错误明确，trace 全链可解析 |
| Adapter | 同 fixture 对 replay 和选定仿真端口 | 坐标/深度/幂等/revision 契约一致；不泄露 oracle |

## 研究实验

固定相同初始 2–3 视角、布局生成器、传感噪声、几何规则、预算、seed 集合和场景拆分。比较 Random View、Max-Visibility、Max-Uncertainty、RoomScout Task-Critical NBV；比较 uncertainty-only 与 uncertainty×decision-impact；验证消融无验证/LLM 自检/几何验证；经验记忆后续比较有/无 prior。

记录每个 run 的配置哈希、dataset/adapter/model/prompt/规则版本、候选及评分分项、反事实效果、观察与动作轨迹。oracle 只用于离线标签和评分。冻结指标实现后再实验，不能看到结果后改标准。

| 指标 | 定义 |
| --- | --- |
| Task Success Rate | 满足任务硬约束且目标达标的任务数 / 全部任务数；blocked 计未成功 |
| Invalid Action Rate | 违反真值硬约束的已尝试动作 / 全部已尝试动作；零动作记 N/A，同时报告覆盖率 |
| Additional Views | 初始视角之外实际完成的观察次数 |
| Travel Cost | 实际相机路径长度（米），另报时间/风险如配置支持 |
| Decision Flip Accuracy | 对标注需要改变/保持决策的案例，重决策与 oracle 标签相符比例；分开报告 flip 与 no-flip |
| Geometry Violation Rate | 最终布局违反至少一条硬约束的任务比例，同时报告无最终布局比例 |
| Final Layout Utility | 固定 utility 版本下的最终分数；失败/blocked 惩罚预先配置，不只统计成功样本 |

报告多 seed 均值、置信区间及逐场景失败轨迹；保留无收益主动观察、错误信念、高覆盖但无决策价值的反例。完整 MVP 需同时满足原文 §19 所有条目，不将本次合同测试当作 MVP 验收。

## 本次验证记录

2026-09-07：本机 Python 3.14 执行 `python3 -m pytest -q`，9 项通过；`python3 -m compileall -q app tests` 通过。尚未验证 Python 3.11 矩阵、PostgreSQL、LangGraph、Mem0 或任何 3D 后端；这些属于 V0 及后续集成验收。

## V0 最新验证

真实 Compose Python 3.12/PostgreSQL 环境 36 项通过，HTTP Demo 与数据库事件/checkpoint 数量核对通过。详情见 [V0 验收记录](V0_ACCEPTANCE.md)；上方 S0 记录保留作历史。

## SceneBeliefService 最新验证

在单模块范围内新增 26 项单元和 4 项真实数据库集成测试，全套 66 项通过（含原 V0 回归）。已通过 HTTP Demo 和 Event Log 精确重建测试；缺少新增空间证据时不跟随 Mock 轮次生成 supported Claim。见 [模块验收](SCENE_BELIEF_SERVICE.md)。
