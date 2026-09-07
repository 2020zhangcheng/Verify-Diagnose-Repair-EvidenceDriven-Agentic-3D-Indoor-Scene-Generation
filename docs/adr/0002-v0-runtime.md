# ADR 0002 V0 可运行基础设施

2026-09-07，采用。根据本轮要求，V0 使用真实 PostgreSQL、Alembic、FastAPI、LangGraph PostgresSaver；所有领域运算明确标记 fake-v0。

- POST /tasks 增加 auto_run（默认 true），在创建事务中同时记录 RUN_REQUESTED。仍返回创建收据；worker 从持久队列取任务，API 不执行 graph。auto_run=false 支持单独 /run。
- V0 的领域投影集中 entity_records（task_id, entity_id, kind, payload, source_event_id），保留所有 Pydantic 类型与证据引用；专用 scene/layout 表在 V1/V2 按 DATABASE.md 迁移。补充 operations 保存节点提交结果及 state patch，解决事件已提交而 checkpoint 未提交的重放。
- V0 单写者使用 PostgreSQL session advisory lock：每次 worker 执行保持专用连接，进程死亡自动释放。每节点在同连接事务中校验所有权；连接失败即停止，不从连接池换连接继续旧执行。该设计替代本阶段时间 lease，避免 Fake 短任务引入不必要心跳。真实长时外部动作前再引入租约/fencing。
- 所有节点输入输出均为 JSON checkpoint 值；Pydantic 模型在领域边界显式重建。增加观察轮次、final_result 和运行状态；不存完整 belief 或图像。
- NEW_OBSERVATION 是第二次 OBSERVATION_CREATED 的语义，另追加兼容生命周期事件 NEW_OBSERVATION；VERIFICATION 使用细分 VERIFICATION_STARTED/PASSED，另追加汇总 VERIFICATION。保留既有事件类型。
- Fake 执行无外部设备副作用，可按稳定 operation_id 确定性重算；不得把此能力宣称为真实设备 exactly-once。加入 validate 节点产生执行后 Fake observation，成功后 TASK_FINISHED。
- 本地 Demo 使用明确的 bearer demo token 与预置 demo 用户/项目。仅绑定 loopback；不提供生产认证。memory worker 只做 DB fake receipts，保留 debounce、重试与租约，不接 Mem0。

V0 查询范围：layouts/unknowns/candidate views 支持版本分页；executed views 和 trace 先提供完整 Fake 快照，无筛选分页，原文扩展接口留后续。新增 /events 游标查询保证全量历史可访问。数据库迁移和 checkpoint setup 使用 owner，API/worker 使用非 owner 的 roomscout_app。
