# Event First 与事件合同

[events.py](../app/contracts/events.py) 为 schema v1 可运行模型。envelope 包含 event_id、task/project/user、run、task 内 sequence、idempotency_key、causation_id、correlation_id、occurred_at、recorded_at、producer 和带 discriminator 的 payload。任务所属身份由服务端填充。recorded_at 是数据库提交侧时间，occurred_at 是事实时间，排序按 sequence。

## 类型

保留原文事件语义，payload.kind 将结构化实体统一为 ENTITY_RECORDED，数据库 type 按下表派生，不允许客户端任意指定 type。该规范化已记录在 ADR 0001 补充项。

| 数据库 type | payload |
| --- | --- |
| USER_MESSAGE | UserMessagePayload |
| TASK_CREATED / RUN_REQUESTED / TASK_FINISHED / TASK_BLOCKED / TASK_FAILED | LifecyclePayload，entity_ids 指向 task/run |
| TOOL_CALL | ToolCallPayload：operation_id、tool_name、结构化 arguments |
| TOOL_RESULT | ToolResultPayload：operation_id、status、result_entity_ids、error_code |
| OBSERVATION_CREATED | ENTITY_RECORDED + Observation |
| BELIEF_UPDATED | ENTITY_RECORDED + SceneBelief |
| LAYOUT_GENERATED | ENTITY_RECORDED + CandidateLayout |
| UNKNOWN_DETECTED | ENTITY_RECORDED + CriticalUnknown |
| VIEW_SELECTED | ENTITY_RECORDED + CandidateView；只在实际选择时发送 |
| DECISION_RECORDED | ENTITY_RECORDED + Decision |
| VERIFICATION_STARTED | LifecyclePayload |
| VERIFICATION_PASSED / VERIFICATION_FAILED / VERIFICATION_UNKNOWN / VERIFICATION_ERROR | ENTITY_RECORDED + VerificationResult，按 status 派生 |
| ACTION_REQUESTED | LifecyclePayload + TOOL_CALL 意图，引用 decision/layout |
| CAMERA_MOVED | LifecyclePayload；详细 MoveResult 由 TOOL_RESULT result_entity_ids 关联 |
| ACTION_EXECUTED / ACTION_FAILED / ACTION_PARTIAL / ACTION_UNKNOWN | ENTITY_RECORDED + ActionResult，按 status 派生 |

候选视角生成列表、评分和预算变化作为节点的结果投影与 TOOL_RESULT 引用落库；不能把尚未选中的视角写成 VIEW_SELECTED。实体 union 拒绝多余字段并有各自稳定 ID；未来扩展需新 schema version 或显式兼容迁移。

## 顺序

1. 接收用户消息：同事务创建 task、追加 TASK_CREATED 和 USER_MESSAGE，投影及 memory job 入库，提交后再调度。
2. 节点或环境调用：追加 TOOL_CALL（动作另有 ACTION_REQUESTED），创建 operation ledger，提交后才能调用外部系统。
3. 获取结果：原始文件先 durable 保存并核对哈希；TOOL_RESULT、实体事件和投影同事务提交。持久化失败时不得推进 belief/graph。
4. 更新 graph state 与 checkpoint，进入下一节点。Final Action 由 ACTION_* 记录实际结果；TASK_FINISHED 在执行后 validate 通过之后出现。

TOOL_CALL arguments 使用 JSON 值是外部工具异构边界，不允许把 SceneBelief 等核心结构改成自然语言字符串；二进制和大模型原文使用 artifact 引用。不能把“动作执行前记录成功结果”当 Event First。

## 故障矩阵

| 崩溃位置 | 恢复 |
| --- | --- |
| 意图提交前 | 没有授权外部动作，重新尝试写意图 |
| 意图提交后、调用前/中 | 查 operation 并 reconcile；确认 not_started 才按同 ID 重试 |
| 外部成功、结果提交前 | 向 adapter 按 operation_id 对账并导入结果，无法确认则 blocked |
| 结果提交后、checkpoint 前 | 查 event/ledger 复用结果，补 checkpoint，不重复调用 |
| checkpoint 后 | 从官方持久 checkpoint 恢复；游标检查确保引用事件已提交 |
| Mem0 成功、receipt 前 | 根据 batch provenance 对账/去重，不盲目重复抽取 |

事件修订追加新事件，旧 schema 用 upcaster 读取。key 冲突时比较请求 hash，内容不一致报 409/领域冲突。事件日志保存原始事实；checkpoint 负责执行恢复，Memory 是异步派生，两者均不能代替事件。

## V0 增量

TaskSpec 加入实体 union，映射 TASK_UNDERSTOOD；CandidateView 可映射 VIEW_CANDIDATE_GENERATED（仅生成）或 VIEW_SELECTED（选择）。新增 NEW_OBSERVATION、VERIFICATION 生命周期汇总。append_event 会验证 type/payload 相符；不接受任意事件名称覆盖实体语义。Fake 节点 TOOL_CALL 先独立提交；实体、TOOL_RESULT、state 投影、memory job 与 operation patch 同事务提交；官方 checkpoint 在后。

## SceneBeliefService 增量

Observation 的可选空间观测字段随 OBSERVATION_CREATED 原样落库。scene.update 的 TOOL_CALL 记录 fusion_config、fusion_version 与输入 state_hash，然后才进行融合；BELIEF_UPDATED 保存真实结构化结果。默认空字段扩展的幂等比较会规范化旧 payload，历史行不会被重写。回放入口 app/scene/replay.py。
