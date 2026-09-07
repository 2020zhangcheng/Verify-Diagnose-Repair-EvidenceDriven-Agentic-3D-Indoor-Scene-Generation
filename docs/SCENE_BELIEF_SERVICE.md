# SceneBeliefService 实现与边界

本次只实现 SceneBeliefService：结构化观测 → 场景对象/区域/Claim → 多视角融合与不确定性 → 新版 SceneBelief。其余研究模块未实现。公共合同扩展及原因见 [ADR 0003](adr/0003-scene-belief-fusion.md)。

## 已完成

- async `StructuredSceneBeliefService.update(previous, observations, task)` 实现冻结的 SceneBeliefService Protocol。
- 由稳定 object_id/region_id/claim_id 关联结构化观测；不读聊天上下文、Task 自然语言或 Memory 来生成物理事实。
- 对象位置、尺寸、单位四元数融合；按配置比较位置/尺寸/旋转差异。相容样本按视角组代表样本的 geometry_confidence 加权；缺失权重只在估计融合中使用配置 fallback，零权重不贡献均值。
- 语义或几何冲突：保留代表几何估计，不把两个明显矛盾的位置取中点；标记 conflicted、提高几何不确定度和跨视角不一致，保留全部来源 Evidence。语义冲突不武断选一个类别。
- Region 显式区分 free/occupied/occluded/unobserved/uncertain。free 需要显式空间观测及质量门槛；旧对象不会因新帧漏检被删除；空对象列表不代表空房间。
- `is_empty` Claim 必须与区域状态一致；缺少空间证据不能成为 supported。其他 Claim 按稳定 subject/predicate/unit 融合，矛盾值保留 conflicted，不自动忘记反证。
- 不确定性保留 semantic_confidence、geometry_confidence、geometry_uncertainty、visibility、occlusion_ratio、observation_count、cross_view_inconsistency，aggregate 权重配置化；缺失原始值不伪造为已知 0。
- 版本、parent、EvidenceRef 和 observation_ids 追踪；重复观测幂等，按位姿聚类统计有效视角，不通过重复图片/新 view ID 虚增计数。
- 纯服务通过注入 loader 获取不可变历史，不依赖 ORM；数据库桥接只读取已落库观测。事件记录完整配置和哈希，可以逐次重建完全相同的 Belief。

## 运行 Demo

```sh
docker compose up -d --build
docker compose exec -T api python scripts/demo_scene_belief.py
```

或调用 `POST /tasks`，请求中加 `"config_id":"scene-belief-v1"`，认证与幂等 Header 沿用 README。

Demo 输入是明确标注的结构化 fixture：第一视角看到书桌，但窗下区域被遮挡；第二视角报告窗下空闲，并给出略有差异的书桌几何。输出会显示：书桌位置融合到两个测量之间、独立视角数 1→2、窗下 Claim uncertain→supported，柜后区域仍然 unobserved。脚本还从 HTTP Event Log 回放并断言 `event_replay_matches=true`。

`fake-v0` 配置保持原行为。新配置中只有 SceneBeliefService 真实：观察来源、布局、Unknown、候选视角/选择、几何验证、动作和记忆仍 Mock。API 的 final_result.mode=fake-v0 表示末端动作仍为模拟；真实融合身份以 belief.fusion_version 和 TOOL_CALL 的 scene.update 为准。

## 配置及幂等

[configs/scene-belief-v1.json](../configs/scene-belief-v1.json) 定义全部融合权重和阈值。公式是启用特征风险的加权平均，不是校准概率。原始 geometry_uncertainty 优先；缺失时只有实际 geometry_confidence 可用于补集估计。aggregate 对缺失特征使用显式配置 missing_feature_risk，输出原始字段仍 None。没有两个有效视角且上游没报告跨视角值时，cross_view_inconsistency 保持 None。

第一次真实融合 TOOL_CALL 固定该任务配置；后续节点从该事件取回配置，不因文件修改偷偷改变同一任务。fusion_version 包含算法名及配置 SHA-256。更换配置应开新任务/epoch。历史冲突来源通过 Observation 事件访问，Belief 不复制整套原始观测。

同一观测集合顺序无关；分批和一次性融合得到相同内容（version/parent 反映实际更新次数，因而可不同）。服务在每次增量更新中核对 prior 与其历史来源一致，缺失历史、复用 ID 修改内容、伪造 Evidence、混合 task/frame/env 都明确失败。

## 当前限制

- 只支持同一静态 environment_revision、已转换到同 frame 的观测。动作后的新环境 epoch 需从 previous=None 重建；本轮不实现动态对象运动模型或坐标变换。
- 上游负责稳定实体 ID、结构化测量和标定；本服务不执行检测/分割、SLAM、数据关联、遮挡推理或可见性预测。
- 位姿聚类只是有效视角分组，不证明统计独立；visibility 取观测最大值，未计算跨帧像素/表面覆盖并集。
- 概率/置信度聚合为保守工程规则，尚无数据集校准。冲突不会通过简单多数投票自动消失；后续需要显式反证/修正协议。
- 房间表面仅做输入盒体合并与去重，不实现墙面识别、平面融合或真实几何验证。区域状态和对象之间的物理碰撞检查仍留给 GeometryVerifier。
- 首版通过回读历史保证确定性，几何冲突成对比较为 O(n²)，面向小规模模块实验；尚非大场景实时融合引擎。

## 测试

```sh
.venv/bin/python -m pytest tests/test_scene_belief.py -q
./scripts/test-compose.sh
```

包括：部分观测、自由空间门槛、缺失观测/几何、重复图片与重复 ID、批次/顺序一致性、语义/几何/区域冲突、证据伪造拒绝、环境/坐标隔离、旧 schema 兼容、配置及 prior 篡改、真实数据库融合链、exact replay、checkpoint 崩溃恢复和全部 V0 回归。

## 本次验收 2026-09-07

- 本机 Python 3.14 和 Docker Python 3.12 + PostgreSQL 全套测试均 **66 passed**；其中新增本模块单元测试 26 项、集成测试 4 项，原 V0 36 项回归通过。
- HTTP Demo task_id：`8f722c67-3ded-4fd2-b8ec-591b9a4b373f`，status=finished，event_replay_matches=true。
- 两次书桌位置测量 x=0 与 x=0.04m，融合 x=0.0225m；不确定性 aggregate 0.25→0.152，有效视角组 1→2。
- 窗下区域 occluded→free，对应 Claim uncertain→supported；柜后区域持续 unobserved。
- 观察与后续节点仍 Mock。以上数值只验证模块实现及因果输入变化，不构成真实感知精度或研究效果结论。
- 完整输出见 [SCENE_BELIEF_DEMO_RESULT.json](SCENE_BELIEF_DEMO_RESULT.json)。测试保留一个原有 Starlette/AnyIO 上游 DeprecationWarning，未影响断言。

## 仿真感知桥接 V1（2026-09-07）

新增 `SimulationPerception.interpret(raw_observation) -> Observation`，复用上述融合实现，未修改公共协议。设计见 [ADR 0005](adr/0005-simulation-perception.md)。

运行真实仿真到 Belief 的独立 Demo：

```sh
.venv/bin/python -m scripts.demo_simulation_belief
```

输出 `outputs/simulation-belief-demo/belief-demo.json` 包含两轮完整 Observation、Belief 和回放验证结果。初始对象 desk/screen，侧移后发现 radiator；desk 离开视野仍保留历史证据，screen 融合两个视角。每个感知结果使用新 Observation ID，不覆盖原始传感器观测。

- 对象关联与完整 OBB 使用仿真 GT；像素级 raycast 决定是否能被观测，完全遮挡对象不输出。
- visibility 是当前视锥内对象无遮挡投影中可见像素比例；occlusion_ratio 是其补集。不是全物体表面覆盖率。融合 Belief 保留历史最佳可见性，当前视角状态查询对应 Observation。
- geometry_confidence/uncertainty 由配置 `configs/simulation-perception-v1.json` 映射可见性，仅为实验质量代理值。几何本身是 oracle 精确值，不声称真实深度重建。
- 感知 artifact 记录 GT 来源、配置、原始观测哈希/ID、CameraPose、可见像素计数。Evidence → 感知 Observation → artifact → 原始 Observation/Camera View 可追溯。
- 不输出未观测物体、不推断自由空间；原有 region/room 融合能力保持，但本感知桥接不生成区域或墙体检测。语义标签暂使用仿真对象 ID；无 VLM、SLAM 或真实数据关联。
- 独立 Demo 不替换现有 Graph 的 fixture；Planning、Memory、视角选择、执行仍沿用原 Mock。Demo 中相机侧移由脚本指定，不是 Active Perception 算法。

新增测试覆盖遮挡对象发现、跨视角融合与漏检保留、Evidence、确定性重跑、原始观测不可变、环境隔离、artifact 篡改拒绝，以及真实 PostgreSQL Event First → 感知 Observation → Belief → exact event replay。原有几何冲突和 uncertainty 测试一并回归。
