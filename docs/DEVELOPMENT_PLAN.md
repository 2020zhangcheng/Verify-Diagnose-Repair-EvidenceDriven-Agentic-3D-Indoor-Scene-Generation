# 开发阶段与依赖顺序

以下目录是 S0 冻结的原始开发规划；V0 基础设施现已实现，具体进展见文末及 V0_ACCEPTANCE.md。原始“后续”标记不再代表当前文件是否存在。

## 推荐目录

```text
room/
  pyproject.toml
  README.md
  docs/                        # 本阶段七份规范 + adr/
  app/
    contracts/                 # models.py interfaces.py events.py，已创建
    agent/state.py             # 已创建
    main.py                    # 后续 FastAPI composition root
    api/                       # 后续 tasks/scenes/observations/agents
    agent/graph.py             # 后续 graph、edges、nodes/
    scene/                     # 后续 belief/objects/uncertainty/geometry
    perception/                # 后续 depth/segmentation/fusion
    planning/                  # 后续 layout/critical_unknown/view_generator/view_scorer
    verification/              # 后续 collision/clearance/accessibility
    environment/               # 后续 base/replay/一个选定仿真后端
    models/                    # 后续 llm/vlm/embeddings adapters
    memory/                    # 后续 recall/mem0_client/extractor/classifier
    db/                        # 后续 models/repositories/postgres
    workers/                   # 后续 agent runner 与 memory_worker
  migrations/                  # 后续 Alembic 业务迁移
  configs/                     # 后续版本化实验、规则、模型参数
  tests/test_contracts.py       # 本阶段合同边界测试
  tests/integration/           # 后续 DB/checkpoint/worker
  tests/scenarios/              # 后续仿真 fixtures、遮挡、回放
  experiments/                 # 后续 baselines、metrics、evaluation
  docker-compose.yml           # V0 再创建
```

原文 app/models 保留给模型 adapter，领域模型集中 app/contracts，避免与 SQLAlchemy db/models 混淆。environment/real_world.py 不提前创建。先维护一个 memory worker 入口，避免 memory/worker 与 workers/memory_worker 两套业务逻辑。

## 按依赖排序的任务

| 顺序 | 阶段 | 依赖 | 任务与完成门槛 |
| --- | --- | --- | --- |
| 0 | S0 本次 | 基线文档 | ADR、七份工程合同、11 个核心模型、8 个核心端口、合同校验；无复杂逻辑 |
| 1 | V0a | 0 | 锁定 Python 与 runtime 依赖，Postgres+pgvector Compose，Alembic、任务/事件/投影/operations/jobs 表；约束与事务测试 |
| 2 | V0b | 1 | thin FastAPI、持久 RUN_REQUESTED、单任务 lease runner、最小 LangGraph、官方 PostgreSQL saver；节点边界 crash 恢复 |
| 3 | V0c | 1–2 | memory job debounce/lease/retry/reconciliation、fake sink，重放不丢事件；不接复杂 Mem0 |
| 4 | V1a | 0–2 | Environment replay/fake 契约、离散相机、确定性几何工具，完整场景只作测试夹具；adapter 不泄露真值 |
| 5 | V1b | 4 | 简单候选生成 ≥3、required_assumptions、碰撞/边界/门区等验证；不投入美学优化 |
| 6 | V2 | 4–5 | 初始 2–3 个部分视角、未知/遮挡 region、claim 证据、多视角冲突、版本更新；不以真实完整场景替代 belief |
| 7 | V3a | 5–6 | counterfactual outcome/规则+结构化 LLM 的决策影响，radiator 等关键未知 fixture |
| 8 | V3b | 7 | 离散 ViewGenerator、task-conditioned score、预算和停止策略；新观察引发合理重决策 |
| 9 | V4 | 2,6–8 | pre-execute verification、幂等执行/对账、post validate、完整 trace；故障恢复且禁止过期验证 |
| 10 | P1 记忆接入 | 3,9 | 最小 Mem0 pgvector adapter、scope、provenance 去重，然后再做 Experience Prior 消融 |
| 11 | 实验 | 9 | 固定场景/seed/预算、四种视角基线与验证消融、复现报告；达到文档 §19 MVP |
| 12 | V5 延后 | 11 + 新 ADR | 真实 RGB-D/机器人与连续视角优化，当前不做 |

优先资源投入 V2→V3→V4；V1 的完整场景用于建立几何测试基线，不改变最终部分观测任务。Mem0 缺席不妨碍核心闭环验收，但事件和 memory job 可靠性仍须先建立。

## 第一实施阶段 V0 准备创建的文件

后续创建 app/main.py、api/tasks.py、agent/graph.py、agent/edges.py、最小 nodes、db/postgres.py、db/models/{tasks,events,operations,memory_jobs}.py、db/repositories/、workers/{agent_runner,memory_worker}.py、memory/fake_sink.py、migrations/env.py 和首个 versions migration、configs/base.yaml、docker-compose.yml、依赖 lock，以及 tests/integration 下事务/checkpoint/job 测试。所有新增应经过当前合同，不在本次提前提供假运行 handler。

## V0 实施进展

V0a/b 已实现真实数据库、API、queue runner、LangGraph/checkpoint 与 Fake 循环；V0c 已实现 DB fake memory sink 的 debounce/lease/retry，尚未接 Mem0。完整当前目录以仓库为准，S0 的“后续”列表是当时规划。V1/V2/V3 真实算法和仿真依然未启动。

## 首个核心研究模块完成

按用户确认的单模块顺序，SceneBeliefService 首版已完成：结构化观测融合、显式不确定性、冲突和证据链、可回放版本更新，单元/集成/Demo 验收通过。仅接入 scene-belief-v1 配置；尚未推进真实 LayoutPlanner、CriticalUnknownDetector、View Utility 或 GeometryVerifier。范围见 ADR 0003 与 SCENE_BELIEF_SERVICE.md。
