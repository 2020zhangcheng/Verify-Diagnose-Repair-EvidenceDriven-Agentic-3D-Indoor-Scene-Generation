# LLM 实战接入

LLM 接收当前实体盒场景和确定性诊断，输出若干独立 MOVE 候选。原 GeometryVerifier 全场景复查，只有改善且不增加错误的候选才修改输出场景。没有规则修复回退；模型配置错误、限流、超时或格式错误明确失败。当前仍是 JSON 盒体场景，非真实设备执行。

## 预留地址与配置

项目根目录 `.env` 已预留以下项：

```dotenv
ROOMSCOUT_LLM_BASE_URL=https://YOUR-PROVIDER.example/v1
ROOMSCOUT_LLM_API_KEY=YOUR_API_KEY
ROOMSCOUT_LLM_MODEL=YOUR_MODEL_ID
```

BASE_URL 填供应商的 API 根地址，客户端追加 `/chat/completions`；不要重复填写该后缀。供应商前缀不含 /v1 的，按供应商文档填写。兼容本地模型服务器；无鉴权服务可以留空 key。在 Docker 内访问 Mac 本机服务，例如 `http://host.docker.internal:1234/v1`，不能写容器自己的 localhost。不要把供应商 Key 当作 RoomScout 的 DEMO_TOKEN。

其他可选项：

```dotenv
ROOMSCOUT_LLM_TIMEOUT_SECONDS=60
ROOMSCOUT_LLM_MAX_TOKENS=1500
ROOMSCOUT_LLM_TOKEN_PARAMETER=max_tokens
ROOMSCOUT_LLM_JSON_MODE=true
ROOMSCOUT_LLM_MAX_CANDIDATES=8
```

部分模型要求 `max_completion_tokens`，修改 TOKEN_PARAMETER；不支持 `response_format` 的兼容服务可设 JSON_MODE=false，但本地 JSON/Pydantic 校验仍强制执行。响应须是非流式 Chat Completions，choices[0].message.content 为 JSON 字符串且 finish_reason=stop。协议参考 [Chat Completions 官方文档](https://developers.openai.com/api/reference/cli/resources/chat/subresources/completions)。不承诺所有供应商的私有协议均兼容。

```sh
docker compose up -d --build
```

填写或修改 `.env` 后执行 `docker compose up -d --force-recreate api`，重新注入环境变量。不要仅在容器内改文件。

- Swagger：`http://127.0.0.1:8000/docs`
- 配置状态：`GET http://127.0.0.1:8000/geometry/config`
- 发起实战：`POST http://127.0.0.1:8000/geometry/repair`
- 工具路由闭环：`POST http://127.0.0.1:8000/geometry/repair-graph`
- 查看结果：`GET /geometry/repairs/{run_id}`
- 查看追加式事件：`GET /geometry/repairs/{run_id}/events?after=0&limit=100`

`/geometry/repair-graph` 按《RoomScout Geometry Critic & Repair Tools》执行
显式 ReAct 闭环：Geometry Critic 后把结构化 `diagnostics` JSON 列表和 8 个
确定性工具的 OpenAI `tools` schema 一起交给 LLM。LLM 只返回工具名、诊断 ID
和策略；循环再执行确定性 Tool、强制 `verify_scene`，必要时
`rollback_repair`。每次验证失败会重新进入 Geometry Critic，ReAct 循环最多
10 轮。旧的 `/geometry/repair` MOVE 接口继续保留给兼容客户端。

全部接口需 `Authorization: Bearer roomscout-local-demo`（若修改过 DEMO_TOKEN，则用自己的值）。配置端点返回是否已配置，不返回 Key，也不声称已探测供应商连通性。

## 最小调用

```sh
curl http://127.0.0.1:8000/geometry/config \
  -H 'Authorization: Bearer roomscout-local-demo'

curl http://127.0.0.1:8000/geometry/repair \
  -H 'Authorization: Bearer roomscout-local-demo' \
  -H 'Idempotency-Key: my-first-llm-run' \
  -H 'Content-Type: application/json' \
  --data-binary @configs/llm-repair-demo-request.json
```

或使用现成脚本（读取本地 `.env` 的 DEMO_TOKEN）：

```sh
.venv/bin/python -m scripts.demo_llm_repair
.venv/bin/python -m scripts.demo_llm_repair --input my-scene.json --max-iterations 5
```

脚本输出 HTTP 状态、run_id、幂等键和本地 response.json 地址。完整结果在 result.scene、result.report、result.actions。此脚本没有 Fake 模型路径；未填写模型配置将返回 503。

请求是 `{"scene": <JSON场景或对象数组>, "max_iterations": 5}`，同时支持上一模块的简写 position_m/size_m 和完整 geometry 格式。上限 100 对象、20 轮。一次请求同步完成；每轮最多一次模型请求，候选由程序分别验证，不逐候选调用模型。请求可能持续数分钟，HTTP 代理和客户端须配置足够超时。响应 status 为 pass/blocked/iteration_limit，不能只看 HTTP 200 判定修复成功。

## 运行记录与错误

Geometry Repair 使用线程安全的进程内 `InMemoryGeometryStore` 保存运行投影和
append-only 事件，不依赖 PostgreSQL、Alembic 或 checkpoint。`USER_REQUEST` 记录后才
开始处理，`LLM_REQUEST` 记录后才发网络请求，`LLM_RESPONSE` 记录后才解析。报告、
接受编辑前的 `ACTION_INTENT`、最终结果和原始模型响应都会留在当前进程内；不记录
Authorization 头或 API Key。几何场景和诊断会发送给所配置的模型供应商。

GET events 通过 next_cursor 分页，可保存 items 为 JSONL 交给 geometry replay_journal；
完成结果的确定性几何/动作可回放，不重新调用模型。模型本身的生成不保证可复现。

同一进程、同一用户、同一 Idempotency-Key、同一请求只创建一次运行；再次 POST 返回
现有状态，不再次调用模型。改请求须改 key，原键异参 409。上游失败返回 502 和 run_id
（例如 llm_http_401、llm_http_429、llm_timeout、llm_invalid_response）；无自动重试或
静默回退，失败也保留事件。进程重启、多个 API worker 或容器替换会丢失进程内运行记录，
因此该存储适合本地同步实验，不提供跨进程耐久性。

本模块独立于 /tasks 的 Fake Graph，不改变 Memory/Planning 协议；几何修复路径使用显式
ReAct 循环，/tasks 的旧版耐久任务仍使用其既有 LangGraph checkpoint。几何实战日志不自动
进入 Mem0。当前 key 仅适合本地 demo 鉴权，不是多租户身份系统。

## 验证范围

HTTP MockTransport 测试验证请求格式、鉴权头、真实解析和修复调用链；内存存储测试验证
事件顺序、幂等、分页和错误记录。测试服务返回预构造响应，因此不能证明你选用模型的
修复能力；配置真实地址和模型后，用上述实战请求验证。

## 本轮验收

- 本地 Python 3.14 全量测试 `98 passed, 30 skipped`；其中 Geometry Repair API 使用进程内存储，另有一个原有 Starlette/AnyIO 弃用警告。
- 已使用用户填入的 DeepSeek 服务（配置模型 deepseek-v4-flash-vision-exp）进行一次真实请求，非测试替身。
- Run ID：c361429c-e9d5-427f-be4c-045f98d5bdb1。一个盒体悬空 0.2 米，模型提出 delta=[0,0,-0.2]，确定性复查 PASS；中心 z=0.7→0.5 米。
- 模型调用 1 次；供应商 usage：prompt_tokens=695、completion_tokens=303、total_tokens=998（completion 含 reasoning_tokens=260）。此结果是单个连通性/闭环样例，不是模型修复准确率评估。
- 实际响应与数据库日志导出在 outputs/llm-live-smoke/，确定性回放通过。API 配置查询及 /docs 在线可访问。
