# Geometry Verifier V1

实现 `app/verification/geometry.py:DeterministicGeometryVerifier`。先产出确定性诊断，再让独立修正 policy 提出位置编辑，全场景重新验证。设计记录见 [ADR 0006](adr/0006-geometry-diagnosis-repair.md)。Planning、Memory、LangGraph 公共协议和旧 EnvironmentAdapter 均未修改。

## 运行

```sh
.venv/bin/python -m scripts.demo_geometry_verifier
# 自己的场景，只诊断：
.venv/bin/python -m scripts.demo_geometry_verifier --input your-scene.json --diagnose-only
# 对副本进行受限 MOVE 修复：
.venv/bin/python -m scripts.demo_geometry_verifier --input your-scene.json --output outputs/my-new-run
```

输出目录必须不存在，避免覆盖旧实验。默认自动创建唯一目录。输出 diagnosis.json、可读 diagnosis.md、events.jsonl；修复模式另有 repaired-scene.json 和 result.json。输入文件不被覆盖。容器运行可使用 `docker compose run --rm api python -m scripts.demo_geometry_verifier`；若要导出产物，需显式挂载输出目录。

## 输入 JSON

可输入对象列表，或 `{ "scene_id": "room", "floor_z_m": 0, "objects": [...] }`。简写示例：

```json
[
  {"object_id":"table","position_m":[0,0,0.5],"size_m":[1,1,1],"support_id":"floor","movable":false},
  {"object_id":"vase","position_m":[0,0,1.35],"size_m":[0.3,0.3,0.5],"support_id":"table"}
]
```

也接受模块 SceneSnapshot 的完整 geometry/pose 格式。统一米、Z-up；position_m 为盒体中心；orientation_xyzw 默认单位旋转。Box 是实体几何，不是任意空心物体的无误差近似。只有坐标、没有尺寸不能判断碰撞。物体 ID 必须唯一，floor 保留为无限水平地面。

- support_id 声明预期支撑物；未提供返回 unknown，未知 ID 返回 fail，不猜测语义支撑关系。
- movable=false 禁止修复移动，但仍检查碰撞和支撑。
- anchored=true 必须 movable=false，表示输入明确提供外部固定约束。
- requires_support=false 显式跳过该物体局部支撑检测；不能使依赖它的对象自动通过支撑链。
- center_of_mass_local_m 可指定盒体局部质心，缺失时明确假设均匀实体盒。

## 诊断合同

`diagnose(SceneSnapshot) -> DiagnosisReport` 为纯函数，无网络和模型调用。报告与输入/配置 SHA-256 绑定，支持逐对象/对象对的 pass/fail/unknown。

| 规则 | 判定与输出 |
| --- | --- |
| collision | 15 轴 OBB SAT；对象对、penetration_depth_m、移动分离向量。轴对齐盒体交集体积精确；旋转 OBB 的 intersection_volume_m3=null，不伪造 AABB 体积。 |
| floor_penetration | 盒体最低点是否穿过 floor_z_m。 |
| support | 轴对齐水平支撑；gap_m、接触面积、COM 投影到接触矩形的有符号 margin；正间隙为悬空，负间隙为穿入支撑体。 |
| support_chain | 已满足局部接触条件的对象，其支撑链是否到达地面或显式 anchor；防止“整个堆叠一起悬空”被局部接触掩盖。 |

容差、支撑 margin、severity 尺度、修复步长上限与轮数都在 configs/geometry-verifier-v1.json。severity 是有界工程诊断量，不是物理风险概率。所有 measurements 的字段名带单位；未知体积或 margin 为 null。

`verify(task, belief, layout, geometry)` 保持原 GeometryVerifier Protocol。geometry 必须含一个 `application/vnd.roomscout.geometry-scene+json` 本地 artifact，hash/revision/frame 必须匹配；先应用候选 Placement 到临时快照，再生成原 VerificationResult。失败报告在 reason 中保存完整结构化 Diagnostic JSON，geometry_artifact 保留输入引用。额外任务约束返回 unknown；几何通过不替代语义或任务验证。部分 SceneBelief 本身不足以证明完整场景无碰撞。

## 修正与回放

`RepairPolicy.propose(scene, report)` 为可替换模块内接口。默认 RuleRepairPolicy 使用报告中的位置修正建议，不使用 LLM。只针对失败诊断的 movable 对象提出 MOVE；每候选全场景复验，不引入新的失败/未知诊断，只接受诊断目标严格改善的方案。同目标优先小位移、稳定排序。默认单步上限 2 米、20 轮；停滞为 blocked，耗尽为 iteration_limit，不把“尝试过”当作通过。

没有 DELETE/SCALE/REPLACE、自动改支撑对象或固定属性。修正只改变 JSON 场景状态，不承诺物体移动路径无碰撞，不执行真实设备。无限地面模型尚无房间边界或任务偏好约束，因此不能把本 Demo 当作可直接执行的布局规划器。

离线 events.jsonl 是追加、flush+fsync 的实验日志，记录 CONFIG、SCENE_INPUT、DIAGNOSIS、REPAIR_PROPOSED、REVERIFICATION、ACTION_INTENT、ACTION_EXECUTED、FINAL_RESULT。接受修改前先写 ACTION_INTENT；日志存储失败传播错误。不是既有数据库 Event schema 的扩展，也不是 LangGraph crash recovery 的替代品。数据库桥接测试仍使用既有 TOOL_CALL → VerificationResult → TOOL_RESULT。

```python
from app.verification.replay import replay_journal
result = replay_journal('outputs/my-new-run/events.jsonl')
assert result.status == 'pass'
```

回放重新计算每次诊断、候选复查和位置编辑，检测配置、场景或动作篡改。已完成日志可回放；本轮不提供未完成修复任务的断点续跑。

## 测试与研究边界

```sh
.venv/bin/python -m pytest tests/test_geometry_verifier.py -q
ROOMSCOUT_INTEGRATION=1 .venv/bin/python -m pytest -q
```

覆盖解析深度/体积、容纳穿透、旋转 OBB、接触容差、悬空、COM 越界、支撑链、固定物体、位移预算、无新增碰撞、确定性、artifact 篡改、日志回放、持久化失败以及 PostgreSQL 的 Event First/诊断 round-trip。

未实现：mesh 精确碰撞、倾斜或旋转支撑、多支点平衡、摩擦/惯量/刚体模拟、VLM/LLM 修复、20 场景基准及 VLM 对照、token 成本实验。几何结论只对输入模型与规则成立，不保证真实世界稳定性。实体盒近似桌子会把桌下空隙当实体；此类场景需后续复合碰撞体或 mesh 后端。

“此前无人研究程序验证及修复”不能作为新颖性结论：[SceneCritic](https://lab-spell.github.io/SceneCritic/)已研究符号评估和规则反馈修正，[PhyScene](https://physcene.github.io/)使用物理/几何约束生成场景。本项目应实验验证结构化 Failure Evidence 对修复准确率、迭代和成本的影响。

## 本轮验收（2026-09-07）

- 本地 Python 3.14 全套 111 passed；容器 Python 3.12 + PostgreSQL 全套 111 passed。新增 24 个单元/离线集成测试和 1 个 PostgreSQL 集成测试。仅有原有 Starlette/AnyIO 弃用警告。
- 本地与容器 Demo 均从 fail 经 3 次接受的 MOVE 到 pass；初始穿透深度 0.2 m、悬空间隙 0.75 m、花瓶支撑 margin -0.1 m。是受控盒体样例结果，不能推广为任意场景修复成功率。
- 本地示例结果：outputs/geometry-demo/0dcebfe299eb40a3801f5cfc64eb34b5/。诊断、原始场景、修复过程及最终场景均可追踪。
- API/Graph 默认配置未切换；本模块以独立命令、兼容 GeometryVerifier port 和数据库集成测试交付。
