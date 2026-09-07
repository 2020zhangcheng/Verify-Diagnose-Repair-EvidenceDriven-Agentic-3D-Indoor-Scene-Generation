# 数据库合同

目标 PostgreSQL 16+、SQLAlchemy 2.x、Alembic、pgvector。以下表格保留完整目标设计；V0 已建立真实数据库与迁移，实际表范围见文末。ID 对外是不透明非空字符串，建议应用生成 UUID；业务 ID 列统一 text，时间 timestamptz，结构化快照 JSONB 经 Pydantic 校验，序号 bigint。金额/评分等按领域单位定义，禁止隐式单位转换。

## 表与约束

所有任务实体通过 task_id 归属项目；访问范围由 tasks → projects → users 校验，不能信任请求提供的 user_id。所有领域投影引用 source_event_id；版本化行只追加，新版本不覆盖历史。

| 表 | 主键及主要列 | 约束/索引 |
| --- | --- | --- |
| users | id, created_at | PK id |
| projects | id, user_id, name | FK user；user_id 索引 |
| tasks | id, project_id, request, status, current_stage, current_belief_id/version | project FK；状态是可重建投影 |
| runs | id, task_id, config_id, seed, lease_until, fencing_token, checkpoint_thread_id | 同任务最多一个活动 lease |
| events | id, task_id, sequence, type, schema_version, payload, idempotency_key, causation_id, occurred_at, recorded_at | UNIQUE(task_id,sequence)、UNIQUE(task_id,idempotency_key)，(task_id,sequence) 分页 |
| operations | id, task_id, run_id, intent_event_id, result_event_id, status, request_hash, fencing_token | 同 operation 重试参数 hash 必须一致 |
| artifacts | id, uri, sha256, media_type, size_bytes, calibration_id | 哈希索引，先 durable artifact 后引用 |
| observations | id, task_id, camera_view_id, environment_revision, payload | camera view FK，operation_id 唯一 |
| camera_views | id, task_id, candidate_view_id nullable, pose, actual_cost, environment_revision | 已执行实际姿态；初始观察无需候选 |
| scene_beliefs | id, task_id, version, parent_version, payload | UNIQUE(id,version)，parent 复合 FK |
| scene_objects | belief_id, belief_version, object_id, payload | 复合 PK 与 belief FK；稳定 object_id 不覆盖旧几何 |
| scene_regions | belief_id, belief_version, region_id, payload | 同上 |
| belief_claims | belief_id, belief_version, claim_id, payload | 同上 |
| candidate_layouts | id, task_id, belief_id/version, score, feasibility, payload | belief 复合 FK |
| layout_assumptions | layout_id, assumption_id, claim_id, belief_id/version, payload | layout/claim 复合关联 |
| critical_unknowns | id, task_id, belief_id/version, impact, uncertainty, payload | outcome/affected layouts 在结构化 payload；关系写入时校验 |
| view_candidates | id, task_id, belief_id/version, score, payload | 当前版本查询索引 |
| verification_results | id, task_id, layout_id, belief_id/version, environment_revision, rule_id, status, payload | 不可变，layout/version 索引 |
| decisions | id, task_id, layout_id nullable, belief_id/version, payload | 证据链入口 |
| actions | id, task_id, operation_id, decision_id, layout_id, payload | operation 唯一，intent/result 分开 |
| memory_jobs | id, scope_key, from_seq, through_seq, status, run_after, attempts, lease_until, lease_token, last_error | 到期 pending 索引；固定 batch_key 唯一 |
| memory_receipts | batch_key, sink_version, external_ids, completed_at | UNIQUE(batch_key,sink_version) |

artifact、证据、assumption、outcome 等 JSONB 内引用不是数据库自动 FK；repository 在同事务验证，并在 V0 增加相应关联表/一致性检查。跨租户引用必须拒绝。迁移需实现复合 task/entity 约束，不能只凭全局 ID 认为访问合法。

## 事务和恢复

T1 用户任务创建：创建 tasks 初始行（满足 FK）→ TASK_CREATED/USER_MESSAGE → 派生任务状态及 memory_jobs → commit → 调度。事件和状态同事务对外可见，“先事件”指在后续处理/外部调用之前提交，不要求先于满足 FK 的空任务行。

T2 节点结果：锁定任务序号行 → 分配单调 sequence → insert event → 写领域投影及 jobs → commit。禁止先更新内存 state 后补写事件。节点 checkpoint 在此之后独立保存；宕机后 checkpoint 落后时用已提交事件/operation 结果补齐，不重复副作用。

events 应用角色仅 SELECT/INSERT，拒绝 UPDATE/DELETE（权限及数据库保护）；修正追加补偿事件。运维迁移角色单独管理。投影可重建，事件不用于存储图像二进制。重建须先做 schema upcast，不能覆写原事件。

memory_jobs 以 user/project/agent 范围 debounce 30–60 秒；running job 的 through_seq 固定，新事件进入下一批。worker 用 FOR UPDATE SKIP LOCKED + lease_token 领租，提交后调用 Mem0，心跳续租，过期可重新领取，只有当前 token 可完成 job。重试指数退避、有上限，终态 dead 可人工重放。事件扫描使用每 task 游标/唯一 batch 防遗漏，不能假设不同任务 sequence 全局有序。

Mem0 外部写成功而 receipt 未提交时必须用 batch provenance 对账；无幂等能力时承认可能重复并去重，不能直接宣称 exactly-once。记忆不可阻塞主循环，recall 超时返回空记忆及可观测降级事件。

业务 schema、LangGraph checkpointer 管理的 schema、Mem0 管理的 schema/collection 分离；Alembic 只拥有业务表。V0 验证官方 PostgreSQL saver setup/恢复，不手写 checkpoint 表替代它。pgvector 的维数由 embedding 配置确定，尚未冻结；换模型另建 collection 并迁移，不能混写不同维数。

## V0 实际物理实现

Alembic 0001 已建立 users、projects、tasks、events、memory_jobs，以及 operations、api_receipts、entity_records、memory_receipts。当前 runs 信息存于 tasks.run_id；单写者使用 session advisory lock。其余上表的专用领域表尚未创建，所有领域对象暂以完整类型化 JSONB 存于 entity_records，source_event_id 指回 append-only event。对应 ADR 0002。

events.envelope 保存 schema/user/project/run/causation/time 完整合同；常用 task_id/sequence/type/payload 独立列。数据库 trigger 拒绝 UPDATE/DELETE/TRUNCATE，应用角色没有 events UPDATE/DELETE/TRUNCATE 权限。迁移角色可维护结构；checkpoint schema 由官方 saver.setup 管理。memory receipt 仅为 DB Fake sink，不代表 Mem0 已接入。attempts>0 的重试 batch 已封口，新事件只合并 attempts=0 的 pending batch。

## 独立几何实战记录（migration 0002）

`geometry_runs`：用户隔离的幂等键、request_hash、运行状态、结果投影与错误。
`geometry_run_events`：以 (run_id, sequence) 为主键的追加式事件，包含模型请求/响应和诊断修复过程。触发器拒绝 UPDATE/DELETE/TRUNCATE，app 角色仅可 SELECT/INSERT。每次写入锁定 run，提交后才执行后续步骤。此表独立于既有 tasks/events/memory_jobs，不改变其协议。
