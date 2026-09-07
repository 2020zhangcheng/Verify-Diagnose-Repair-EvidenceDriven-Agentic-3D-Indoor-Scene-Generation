# ADR 0001 工程合同基线

日期：2026-09-07。状态：本阶段采用的工程补全；算法、后端选择和评分参数保持待定。
依据：用户提供的《RoomScout 3D Agent 开发设计文档》v1.0（2026-09-07），已读取全部 20 节、附录 A/B 和总体架构图。仓库初始为空，无已有代码或 AGENTS.md。原文优先级与用户当前阶段要求冲突时，以用户要求为准。

## 审查结论

研究架构无明显不可实现之处；以下问题需在实现之前消除歧义。所有补全均保留 Partial Observation → Scene Belief → Candidate Layout → Critical Unknown → Task-Critical View → Observation → Belief Update → Replanning → Verification 目标。

| 来源 | 问题 | 决策与影响 |
| --- | --- | --- |
| §4 图及节点表 | move_and_observe 的 END 连线歧义；understand_task 未进入主图；失败和无收益分支缺失 | 新观察只回到 belief update；增加任务理解、预算耗尽、不可验证、执行结果未知分支；不能提前 END |
| §4.1 | 全量对象列表与“尽量保存 ID”并存 | checkpoint TypedDict 保存版本引用、有限评分/决策，不放图像或完整历史；领域对象由 Pydantic 定义 |
| §5–6 | 坐标系、姿态、未知值、区域和 claim 标识未定义 | 米、右手 Z-up、xyzw 单位四元数；unknown 不等于 free；显式 claim、region 和证据引用 |
| §9、17、19 | 2–3 个候选、V1 ≥3、MVP ≥2 | 合同至少 2 个；默认目标 3 个。无法产生 2 个则显式规划不足，不复制候选充数 |
| §11 | passed 二值不能表示缺失几何；场景变化后旧验证可能误用 | pass/fail/unknown/error 四态；绑定 belief、layout、environment revision、规则集；只有全部必要硬约束 pass 且版本仍有效才能执行 |
| §14 | 数据库与外部动作不能形成单一事务；恢复可能重复动作 | 意图先提交，operation_id 幂等调用，结果先持久化再推进；状态不明先 reconcile，不能直接重复执行；不承诺跨系统 exactly-once |
| §13–14 | events、投影、checkpoint 的一致性边界未定义 | 事件与本地投影/job 同一事务，checkpoint 为独立持久化；恢复用事件游标与 operation ledger 对齐；每任务单写者和 fencing token |
| §12、17、附录 B | V0 worker 与 P1 Mem0 的实现顺序不一致 | V0 建 durable jobs/debounce/lease 和 fake sink；真实 Mem0 最小接入延后；不先做复杂提取分类 |
| §8、15 | 几何 adapter 可能泄露仿真完整真值 | planner/VLM 仅获得观测与 belief；完整真值只供隔离评估；测量有范围、来源与环境版本，不支持时返回 unknown |
| §10、13 | 决策、claim、执行意图无稳定关系 | 补充 decisions、belief_claims、operations、runs、artifact 元数据及版本化关联；不修改原表职责 |

## 外部可行性核对

LangGraph 支持持久 checkpoint，但副作用仍需幂等与重放控制；checkpoint 不替代事件账本。[官方 persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[官方 functional API](https://docs.langchain.com/oss/python/langgraph/functional-api)。Mem0 支持 pgvector，因此同 PostgreSQL 技术方向可行；业务、checkpoint、Mem0 独立 schema 和迁移所有权。[Mem0 官方向量存储](https://docs.mem0.ai/components/vectordbs/overview)。核对日期 2026-09-07；不在本阶段宣称已验证整套依赖兼容。

## 尚未冻结

仿真器（Blender/Habitat 等）、融合算法、反事实计算方法、视角权重/阈值、规则容差、embedding 模型与维数、Mem0 版本。后续各自 ADR + 可复现实验后决定，当前仅冻结输入输出及可追溯要求。

## 事件编码补充

在建立事件代码前冻结：实体类事件使用统一 ENTITY_RECORDED payload 与结构化 entity union，数据库 type 由实体类型/状态确定，保留原文 OBSERVATION_CREATED 等语义名称。增加成功、unknown、error、决策和动作意图事件以覆盖失败/恢复分支。映射见 EVENT_SCHEMA.md；不改变 append-only 与 Event First。

## 基线溯源

原件路径：`/Users/initial/Downloads/RoomScout_3D_Agent_开发设计文档.docx`。SHA-256：`3110fc35d73d3eb503887e47a21a84815ec54d988d0173f71e785db5bc058eec`。原件未修改。
