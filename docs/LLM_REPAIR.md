# Geometry Critic ReAct Loop

## 目标

修复流程是一个显式、有界的 ReAct 循环：

1. `Geometry Critic` 对当前不可变 `SceneSnapshot` 生成结构化诊断。
2. `GeometryRepairRouter` 将失败诊断 JSON 和完整八项 `tools` 列表一起发送给 LLM。
3. LLM 只能选择一个允许的 tool、诊断 ID、目标对象和策略。
4. `RepairToolRegistry` 校验选择，确定性 tool 根据几何证据计算变换。
5. 执行后立即重新验证；不通过时回到 Critic，重新诊断和选 tool。
6. 新违规或严重度没有改善时回滚本轮动作，再回到 Critic。
7. PASS、阻塞或达到 10 轮时结束。

实现入口是 `app/repair/loop.py:run_repair_loop`。循环状态只在一次调用内存活，
不依赖外部 checkpoint。

## LLM 边界

模型收到两份相同的工具信息：

- Chat Completions 请求顶层的 `tools` 字段；
- user message 中的 `tools` JSON 列表。

user message 还包含当前 `scene_revision`、场景对象的可移动性/支撑关系、失败
诊断和最近修复历史。模型不接收用于自由编辑的位移参数，确定性 tool 计算
`delta_m` 或目标姿态。

Registry 会拒绝未知工具、非失败诊断、Critic 未允许的工具、锁定对象、错误
目标字段和过期场景版本。`verify_scene` 与 `rollback_repair` 是协议中的控制
工具，但只由循环内部执行。

## 事件

`POST /geometry/repair` 会按顺序记录 `USER_REQUEST`、`CONFIG`、`SCENE_INPUT`、
`DIAGNOSIS`、`LLM_REQUEST`、`LLM_RESPONSE`、`LLM_PARSED`、`TOOL_SELECTED`、
`ACTION_INTENT`、`ACTION_EXECUTED`、`REVERIFICATION`、`REACT_OBSERVATION` 和
`FINAL_RESULT` 等事件。事件序号由 `InMemoryGeometryStore` 分配，分页接口为：

`GET /geometry/repairs/{run_id}/events?after=0&limit=100`

当前存储是线程安全的进程内内存，重启会丢失运行记录；这是当前版本的预期行为。
