# RoomScout Geometry Repair

RoomScout 的 HTTP 入口是 Geometry Critic 修复闭环，同时保留独立的场景信念、
模拟环境和感知数学模型：

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

## 数学建模模块

以下模块是纯 Python、无外部服务依赖的确定性模型：

- `app/contracts/models.py`：场景、观测、信念、约束和验证合同。
- `app/environment/geometry.py`：四元数坐标轴、OBB/SAT、射线盒体相交和房间边界。
- `app/environment/minimal.py`：相机移动、RGB/深度生成、遮挡、碰撞和会话幂等。
- `app/perception/simulation.py`：基于模拟真值的结构化观测生成。
- `app/scene/geometry.py`、`app/scene/belief.py`：跨视角盒体融合、冲突检测、证据
  链和不确定性评分。

这些模块作为库和离线 Demo 使用；HTTP 服务仍专注于 Geometry Critic 修复。
数学公式和模型边界见 [MATHEMATICAL_MODELS.md](docs/MATHEMATICAL_MODELS.md)。

## 测试与演示

```sh
.venv/bin/pytest -q
python3 -m scripts.demo_llm_repair \
  --input configs/geometry-react-bad-demo.json \
  --api http://127.0.0.1:8000

python3 -m scripts.demo_simulation_belief
python3 -m scripts.demo_scene_belief
```

默认运行产物放在 `output/`，已加入 `.gitignore`。可复现的坏场景和 Critic 配置
保留在 `configs/`。
