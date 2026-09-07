# ADR 0007 — 可配置 LLM 修复与独立 HTTP 运行

本轮用户授权接入 LLM，预留服务地址供实战。保留 RepairPolicy、GeometryVerifier 和 Planning/Memory/LangGraph 协议，新增 Chat Completions 兼容的 LLMRepairPolicy，仅输出 MOVE JSON。确定性程序继续拥有最终判定权，不因模型声称 pass 而放行。

新增 POST /geometry/repair、GET /geometry/config、GET /geometry/repairs/{id}、GET /geometry/repairs/{id}/events。同步请求适合小场景；几何运行使用独立 geometry_runs/geometry_run_events，避免旧 worker 抢占。不修改既有 Event envelope；新模块事件在新 append-only 表持久化（含配置、请求、原始模型响应、usage、诊断、提案、动作和结果）。不进入 Memory 后台链路。

幂等键按用户隔离，创建提交后才调用模型；重复请求返回现有运行状态/结果。崩溃中断的 running 请求不自动重试，防止不确定的外部调用被重复计费。可以检查已落库事件并用新键重新实验，本轮无自动 crash recovery。

地址、API key、模型与超时仅服务端环境配置，不从 API 请求指定，SecretStr 不进入日志。失败明确返回，不退回规则策略。默认不联网，未配置 URL/模型时 503。无需密钥的本地兼容服务允许空 key。模型返回仍严格 Pydantic 验证，位置/对象/诊断/位移约束由原修复执行器检查。
