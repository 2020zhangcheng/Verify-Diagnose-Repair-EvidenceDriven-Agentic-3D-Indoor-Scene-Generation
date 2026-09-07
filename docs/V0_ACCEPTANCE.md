# RoomScout V0 验收记录

日期：2026-09-07。V0 工程闭环验收通过，领域能力仍全部为 fake-v0。

## 实际执行

- `docker compose up -d --build` 成功：PostgreSQL 16 + pgvector、迁移初始化、API、Agent worker、Fake memory worker。
- `docker compose exec -T api python scripts/demo.py` 成功：HTTP 创建任务，worker 完成一次主动观察条件循环，执行并复核。
- `./scripts/test-compose.sh` 在容器 Python 3.12、独立 roomscout_test PostgreSQL 数据库运行：**36 passed**。
- 本机 Python 3.14 同套测试：**36 passed**。
- 有一个来自 Starlette/AnyIO 的上游 DeprecationWarning，不影响运行或断言；未屏蔽警告。

## 持久化证据

Demo task_id：`bd093aa1-c070-406c-b42c-16ca65f97ace`。
run_id：`b7401f85-f2a3-4752-b104-aaae99e50055`。

结果 `status=finished`，`stage=finished`，`final_result.validated=true`，`mode=fake-v0`；Scene Belief version=2。
SQL 查询确认该任务有 **61 条事件、17 个 PostgreSQL checkpoint**，API trace 包含 24 个节点。完整 Demo 输出见 [V0_DEMO_RESULT.json](V0_DEMO_RESULT.json)。

核心顺序：

```text
TASK_CREATED → USER_MESSAGE → RUN_REQUESTED
→ OBSERVATION_CREATED → BELIEF_UPDATED
→ LAYOUT_GENERATED ×3 → UNKNOWN_DETECTED
→ VIEW_CANDIDATE_GENERATED ×2 → VIEW_SELECTED
→ CAMERA_MOVED → OBSERVATION_CREATED → NEW_OBSERVATION
→ BELIEF_UPDATED → LAYOUT_GENERATED ×3
→ VERIFICATION_STARTED → VERIFICATION_PASSED ×3 → VERIFICATION
→ ACTION_REQUESTED → ACTION_EXECUTED
→ OBSERVATION_CREATED（执行后复核）→ TASK_FINISHED
```

节点 TOOL_CALL/TOOL_RESULT 和 DECISION_RECORDED 也已持久化；上面只展示主链。API/worker 实际数据库身份为 `roomscout_app`，events 权限检查为 INSERT=true、UPDATE=false、DELETE=false、TRUNCATE=false。

## 覆盖的故障边界

测试包括事务回滚、幂等冲突、数据库 append-only trigger、条件预算停止、校验缺项/失败/过期拒绝执行、checkpoint 暂停恢复、四种节点结果提交后崩溃、子进程在 execute 后直接退出、并发 runner 单写者、trace 引用完整、memory job 租约过期与重试批次封口。

进程退出后新进程重新连接真实 PostgreSQL，复用已提交 operation patch 与 checkpoint；ACTION_EXECUTED 始终只出现一次。此结论限定于无外部副作用的 Fake 适配器，不是对真实机器人执行语义的保证。

## 未实现的后续能力

真实 3D 传感/渲染、信念融合算法、布局算法、反事实评分、任务相关视角收益算法、确定性几何引擎和 Mem0 均未实现。V0 只证明事件、状态、控制流、checkpoint、恢复和查询链路正确。专用领域表拆分、生产鉴权和真实设备 fencing 按 ADR 0002 留待后续。
