# RoomScout V0

真实 FastAPI → PostgreSQL Durable Event → LangGraph → PostgreSQL Checkpoint → Final Result。领域节点全部是明确标记的 Fake 数据；不包含真实 3D 感知、布局算法、视角评分算法或 Mem0 调用。

## 直接启动

```sh
docker compose up -d --build
```

Compose 启动 PostgreSQL 16 + pgvector、一次性 Alembic/checkpoint 初始化、API、Agent worker、Fake memory worker。数据库使用持久卷；API 与数据库仅绑定本机地址。预置 `demo-user`、`demo-project`；本地演示 Token 为 `roomscout-local-demo`，可通过 `.env` 的 `DEMO_TOKEN` 修改。应用使用受限 `roomscout_app` 数据库账号，迁移使用独立所有者账号。这里只提供本地 Demo 认证。

[交互式 API 文档](http://localhost:8000/docs)；健康检查 `GET /health`。

```sh
curl -X POST http://localhost:8000/tasks \
  -H 'Authorization: Bearer roomscout-local-demo' \
  -H 'Idempotency-Key: desk-demo-001' \
  -H 'Content-Type: application/json' \
  -d '{"request":"把书桌移动到窗户附近"}'
```

返回 201 创建收据。默认 `auto_run=true`，创建事务同时持久化运行请求；独立 worker 自动取队列。用返回的 task_id 查询：

```sh
curl -H 'Authorization: Bearer roomscout-local-demo' http://localhost:8000/tasks/TASK_ID
curl -H 'Authorization: Bearer roomscout-local-demo' http://localhost:8000/tasks/TASK_ID/events
curl -H 'Authorization: Bearer roomscout-local-demo' http://localhost:8000/tasks/TASK_ID/trace
```

也可创建时设置 `auto_run:false`，再 `POST /tasks/TASK_ID/run`，请求体 `{ "resume": true }` 并传新的 Idempotency-Key。同 key 同 body 返回原收据；不同 body 返回 409。

完整自动 Demo（创建、轮询、校验事件链、查询 trace）：

```sh
docker compose exec -T api python scripts/demo.py
```

脚本在容器内调用 localhost:8000。输出包含任务 ID、final_result、全部事件和 trace 节点数。终态必须是 `finished`，`final_result.mode` 是 `fake-v0`。

## 真实数据库测试

```sh
./scripts/test-compose.sh
```

脚本创建/复用独立 `roomscout_test` 数据库，运行迁移和全部 pytest，不删除主库或原事件。测试覆盖创建、幂等、append-only、事务回滚、条件循环、PostgreSQL checkpoint、结果提交后崩溃、进程直接退出恢复、并发 runner、trace 与 Fake memory 租约恢复。

数据库直接查看完整事件链：

```sh
docker compose exec -T db psql -U roomscout -d roomscout \
  -c "SELECT task_id, sequence, type FROM events ORDER BY task_id, sequence;"
```

`NEW_OBSERVATION` 是主动视角得到第二次 `OBSERVATION_CREATED` 后的兼容标记；`VERIFICATION` 是规则级结果的汇总。执行后额外观察用于 Fake validate，然后才产生 TASK_FINISHED。事件、投影和 memory job 同事务；事件 UPDATE/DELETE/TRUNCATE 由数据库拒绝。

## 本地开发

Python 3.11+，Compose 固定 Python 3.12。依赖版本位于 requirements.lock。

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e .
cp .env.example .env
.venv/bin/python -m pytest -q
```

未开启 `ROOMSCOUT_INTEGRATION=1` 时集成测试明确跳过；普通 pytest 不等于 V0 验收。要跑全部测试，优先使用上面的 Compose 测试脚本。Mac 若 Docker 报找不到 docker-credential-desktop，把 `/Applications/Docker.app/Contents/Resources/bin` 加入 PATH。

## 工程边界

- [架构](docs/ARCHITECTURE.md)、[V0 ADR](docs/adr/0002-v0-runtime.md)、[开发计划](docs/DEVELOPMENT_PLAN.md)。
- app/api：薄 HTTP 入口；app/db：SQLAlchemy、事件/投影仓储；migrations：固定 Alembic 历史。
- app/agent：状态、Graph、条件边、Fake nodes；app/environment：Fake adapter。
- app/workers：durable task runner 与后台 Fake memory sink；app/contracts：领域合同。
- configs/fake-v0.json：固定演示配置说明；V0 只接受 fake-v0，不支持任意配置切换。

当前 entity_records 保存完整类型化领域投影；专用 scene/layout 表在后续阶段拆分。session advisory lock 保证 V0 每任务单写者；真实设备的租约/fencing 和外部状态对账留待引入外部副作用时实现。Fake 几何通过只证明系统路由及验证门禁，不代表真实空间可执行。

## SceneBeliefService 首版模块

已新增真实的结构化观察融合服务；原 V0 配置保持可用。新模块 Demo：

```sh
docker compose up -d --build
docker compose exec -T api python scripts/demo_scene_belief.py
```

仅信念层真实，观测输入与后续研究模块仍 Mock。详细能力、可复现策略和限制见 [SceneBeliefService](docs/SCENE_BELIEF_SERVICE.md)。
