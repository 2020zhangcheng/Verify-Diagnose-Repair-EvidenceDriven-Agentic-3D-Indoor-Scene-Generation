# API 与模块接口合同

本文件定义基线 FastAPI 合同；V0 已提供可调用服务，当前支持范围和 Demo 认证见文末。领域端口可导入，见 [interfaces.py](../app/contracts/interfaces.py)。

## HTTP

基础路径沿用原文，无额外版本前缀；请求/响应 schema_version=1。鉴权身份来自认证上下文，user_id 不允许由请求指定；project_id 必须授权。跨用户项目/任务统一 404。所有 POST 必须有 Idempotency-Key；同作用域同 key 同请求返回原结果，不同请求返回 409。作用域为身份+method+path，run 新尝试需新 key。

| 方法与路径 | 请求 | 响应 |
| --- | --- | --- |
| POST /tasks | `{schema_version:1,project_id,request,config_id,seed}` | 201 `{schema_version:1,task_id,status:"created",stage:"created",event_id}`；请求事件提交后响应 |
| GET /tasks/{id} | 无 | 200 `{schema_version:1,task_id,status,stage,run_id,belief_ref,last_event_sequence,blocked_reason}` |
| POST /tasks/{id}/run | `{schema_version:1,resume:true}` | 202 `{schema_version:1,task_id,run_id,status:"queued",event_id}`；durable RUN_REQUESTED 后才接受 |
| GET /tasks/{id}/belief | 可选 version；无则当前 | 200 `{schema_version:1,task_id,belief:SceneBelief|null}`；不存在的指定版本 404 |
| GET /tasks/{id}/layouts | belief_version,cursor,limit | 200 Page[CandidateLayout] |
| GET /tasks/{id}/unknowns | belief_version,status,cursor,limit | 200 Page[CriticalUnknown] |
| GET /tasks/{id}/views | kind=candidate/executed,cursor,limit | 200 Page[CandidateView 或 ExecutedView]；默认 candidate |
| GET /tasks/{id}/trace | decision_id 可选，cursor,limit | 200 `{schema_version:1,task_id,nodes,edges,next_cursor}` |

Page 为 `{schema_version:1,items:[],next_cursor:null,belief_ref:null|BeliefRef}`，limit 默认 50，范围 1–200；按稳定 ID/事件 sequence 游标，游标绑定查询与版本，不能翻页过程中混入新 belief。ExecutedView 为 `{camera_view_id,candidate_view_id:null|str,camera_pose,environment_revision,observation_ids,actual_cost_m}`。同一 run lease 存活时返回已有 run（202）；终态 finished 再 run 为 409；failed/blocked 恢复需原任务条件仍有效，状态不可恢复返回 409。

Trace node：`{id,kind,entity_id,version:null|int}`；kind 为 action/decision/layout/assumption/claim/belief/observation/camera_view/verification，edge 为 `{from,to,relation}`。必须走数据库关系，缺失引用显式标记 integrity error，不由 LLM 拼解释。决策链包含 Action → Decision → Layout → Assumption → Claim@Belief → Observation → CameraView，Verification 同时关联 Layout/Belief/Observation。

错误统一 `{schema_version:1,error:{code,message,retryable,correlation_id,details:{}}}`。400 游标错误，401 未认证，404 不存在/不可访问，409 幂等参数冲突/版本冲突/任务状态冲突，422 字段校验失败，503 事件库或调度持久化不可用。不能在事件未提交时返回成功。run 为异步，任务算法失败经 GET status/trace 查询，不冒充 HTTP 请求失败。

## 端口语义

| 端口 | 输入 → 输出 | 责任 |
| --- | --- | --- |
| EnvironmentAdapter | observe/move_camera/get_depth/measure_geometry/detect_collision/execute_layout/reconcile | 统一环境访问；actual pose、operation ID、revision；unsupported 不捏造结果 |
| SceneBeliefService.update | previous + observations + task → 新 SceneBelief | 融合留痕、保留冲突、稳定实体 ID、版本单调 |
| LayoutPlanner.generate | task + belief + target_count → layouts | 默认 3，最少 2；保留假设与约束，候选不足报领域错误 |
| CriticalUnknownDetector.detect | task + belief + layouts → unknowns | 结构化可能结果与决策差异；空结果必须来自评估而非忽略缺数据 |
| ViewGenerator.generate | task + belief + unknowns + current_pose → views | 第一版离散位姿，目标关联，不直接调用仿真真值 |
| ViewSelector.select | task + belief + layouts + unknowns + views + budget → ViewSelection | 记录所有评分分项；允许无视角及原因；不以最大覆盖替代决策收益 |
| GeometryVerifier.verify | task + belief + layout + GeometryEvidence → results | 每项必要规则都返回结果，缺数据 unknown，确定性、可重复 |
| MemoryService.recall/ingest | scoped query / event IDs + batch key → MemoryRecords | recall 可降级；ingest 仅后台 worker 调用；记忆不改变物理事实 |
| LLMAdapter/VLMAdapter | 结构化 context/Observation + 输出模型类型 → 校验模型 | 保存 prompt/model 版本、输出与调用事件；格式错误重试有上限 |

所有端口 async，算法端口不直接持久化；application 层围绕端口执行事件事务。异常分类冻结为 InvalidInput、StaleRevision、UnsupportedCapability、RetryableDependencyFailure、PlanningInsufficient、OperationOutcomeUnknown（本阶段为语义合同，异常类待实现）。unknown/fail 等正常领域结果不是异常。

外部副作用传 OperationContext：operation_id 在重试/恢复时保持不变，fencing_token 随执行所有权递增；旧 writer 不得继续调用。expected_environment_revision 不符拒绝。execute_layout 再检查通过结果覆盖所有 task/layout 硬约束及同版本 decision，不能仅检查 verification_ids 非空。reconcile 返回 unknown 时停止自动执行；succeeded 时读取持久 result_event，缺失结果先对账导入。

## V0 已实现范围 2026-09-07

由 ADR 0002 启用 POST /tasks 默认 auto_run；新增 GET /tasks/{id}/events（after 序号与 limit 分页）及 /health。任务状态增加 final_result，明确 mode=fake-v0。API 使用 demo bearer token，身份固定为预置 demo-user；不是生产认证服务。

已实现 task/create/run/status、belief 版本、layout/unknown/candidate view 按 belief 分页、完整 trace 快照、已执行视角快照。V0 executed views 与 trace 为小规模 Fake 快照，暂不提供游标分页/筛选；需分页历史时使用 events。其余 HTTP 路径行为沿用上文。领域推理全为 Fake，execute 的校验门禁是真实代码，规则结果来自 Fake fixture。

## SceneBeliefService 配置增量

POST /tasks 的 config_id 新增 scene-belief-v1（默认仍 fake-v0）。HTTP 路径不变。该配置只把信念层接到真实融合服务，其余模块仍 Mock；旧客户端读取新 Observation 扩展字段时需要更新严格模型。见 ADR 0003。

## 独立 LLM 几何实战 API（ADR 0007）

新增 `/geometry/repair`、`/geometry/config`、`/geometry/repairs/{run_id}` 和事件分页接口。原 `/tasks` API 不改变。请求、鉴权、幂等与错误约定详见 [LLM_REPAIR.md](LLM_REPAIR.md)。此运行不由旧 LangGraph worker 接管。
