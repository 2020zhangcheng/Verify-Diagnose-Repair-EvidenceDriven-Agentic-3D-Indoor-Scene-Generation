# 数学建模层

仓库现在包含三类确定性数学模型：Geometry Critic 修复模型、可选的
Functional Critic 功能可用性模型和 SceneBelief 感知融合模型。它们都是本地
Python 模块，不需要外部服务。

## 几何模型

每个物体用盒体 `B = (p, q, s)` 表示：位置 `p`、四元数姿态 `q` 和尺寸
`s=(sx, sy, sz)`。四元数先转换为三个世界坐标轴，盒体角点用于计算 AABB。

碰撞检测使用分离轴定理（SAT）：在两组盒体局部轴以及轴叉积构成的候选轴上
投影；任一轴投影不重叠即判定分离。若全部重叠，则按最小穿透深度生成确定性
分离向量。支撑检查计算：

- 物体底面与地面/支撑体顶面的高度差 `gap`；
- XY 投影交叠面积；
- 质心投影到支撑区域边界的最小距离 `margin`。

这些量经过容差和移动预算约束后，由 repair tool 计算新场景。LLM 不直接提交
`dx/dy/dz`。

实现位置：`app/environment/geometry.py`、`app/verification/geometry.py`、
`app/repair/tools.py`。

## 功能模型

带 `affordance` 或 `functional_relations` 的场景会在 Geometry Critic 通过后
进入 `FunctionalCritic`。它用人体导航圆柱、交互锚点、接近距离/朝向、功能净空、
操作扫掠和对象关系构造确定性 Diagnosis；功能通过表示至少存在一个满足当前
V1 约束的交互姿态。功能修复不能绕过 Geometry Critic，修复后会重新执行两层
诊断。没有功能描述的旧场景不会被强制判为 UNKNOWN。

实现位置：`app/verification/functional.py`、`app/verification/models.py`、
`configs/functional-verifier-v1.json`。

## 场景信念与不确定性模型

`SceneBeliefService` 按相机位置和姿态将观测分组，每个视角选取代表性证据，
再按几何置信度加权融合位置、尺寸和姿态。跨视角差异超过配置阈值时保留冲突，
不进行静默平均。

不确定性评分是可解释的加权风险分数，不声称是 Bayesian posterior：

`score = Σ(weight_i × feature_i) / Σ(weight_i)`

缺失特征使用配置中的缺失风险；遮挡、几何不确定性和跨视角不一致会直接进入
风险分数。实现位置：`app/scene/geometry.py`、`app/scene/uncertainty.py`、
`app/scene/belief.py`。

## 模拟感知模型

`MinimalEnvironmentAdapter` 使用静态盒体场景生成 RGB、相机深度和校准工件。
`SimulationPerception` 对每个像素发射相机射线，使用射线—盒体区间求交和最近
命中物体计算可见像素，再生成带证据链的结构化观测。

配置和可复现 Demo 位于 `configs/minimal-room.json`、
`configs/scene-belief-v1.json`、`configs/simulation-perception-v1.json`。
