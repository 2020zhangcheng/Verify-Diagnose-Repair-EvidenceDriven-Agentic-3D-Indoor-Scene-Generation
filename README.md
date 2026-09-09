# RoomScout Geometry Repair

RoomScout 是一个只保留 Geometry Critic 修复闭环的 FastAPI 服务：

`Geometry Critic → LLM 选择 tool → 确定性 tool 执行 → 验证`

ReAct 循环由普通 Python `while` 循环显式编排，最多执行 10 轮。LLM 只能从
Geometry Critic 给出的允许列表中选择工具、对象和策略；位移、姿态和最终
PASS/FAIL 均由确定性代码计算。

## 启动

模型配置放在 `.env`：

```dotenv
ROOMSCOUT_LLM_BASE_URL=https://api.example/v1
ROOMSCOUT_LLM_API_KEY=your-key
ROOMSCOUT_LLM_MODEL=your-model
```

本地运行：

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/uvicorn app.main:app --reload
```

或使用 Docker：

```sh
docker compose up --build
```

健康检查：`GET http://localhost:8000/health`；交互式文档：
`http://localhost:8000/docs`。

## 调用

```sh
curl -X POST http://localhost:8000/geometry/repair \
  -H 'Authorization: Bearer roomscout-local-demo' \
  -H 'Idempotency-Key: repair-demo-001' \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c 'import json; print(json.dumps({"scene": json.load(open("configs/geometry-react-bad-demo.json")), "max_iterations": 10}))')"
```

请求中的 `scene` 也可以直接使用对象列表；每个对象至少包含
`object_id`、`position_m`、`size_m` 和 `support_id`。`max_iterations` 范围为
1–10，默认 10。

响应包含 `run_id`、最终 `result` 和事件查询地址：

```sh
curl -H 'Authorization: Bearer roomscout-local-demo' \
  http://localhost:8000/geometry/repairs/RUN_ID/events
```

事件和幂等记录使用线程安全的进程内内存存储。服务重启或多进程部署后，历史
记录不会保留；这是当前单进程内存版的明确边界。

## 八个工具

工具定义位于 `app/repair/catalog.py`，确定性实现位于 `app/repair/tools.py`：

1. `resolve_collision`
2. `repair_support_contact`
3. `stabilize_support`
4. `repair_boundary`
5. `repair_clearance`
6. `repair_orientation`
7. `verify_scene`
8. `rollback_repair`

其中 `verify_scene` 和 `rollback_repair` 由 ReAct 循环控制，LLM 不能直接调用。
当前盒体 V1 数据只实现了碰撞、支撑和地面边界的确定性修复；未实现的能力会
被安全拒绝，不会让模型自行编造坐标。

## 测试与演示

```sh
.venv/bin/pytest -q
python scripts/demo_llm_repair.py \
  --input configs/geometry-react-bad-demo.json \
  --api http://127.0.0.1:8000
```

默认运行产物放在 `output/`，已加入 `.gitignore`。可复现的坏场景和 Critic 配置
保留在 `configs/`。
