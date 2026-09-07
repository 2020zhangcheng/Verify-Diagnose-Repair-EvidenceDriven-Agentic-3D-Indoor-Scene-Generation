# Agent 状态与领域合同

代码权威定义：[models.py](../app/contracts/models.py)、[state.py](../app/agent/state.py)。全部 Pydantic 模型为 schema_version=1，拒绝额外字段与 NaN/Inf；使用不可变 tuple；时间必须有时区，持久化统一 UTC。

## 状态

RoomScoutState 是 TypedDict，字段完整初始化；初始 belief/decision/selected_view/task_spec/action 均为 None，列表为空，stage=created，last_event_sequence=0。TypedDict 本身不做运行时校验，runner 后续须验证反序列化和实体引用。checkpoint 序列化 BeliefRef/Decision/Budget 为 JSON，恢复显式重建模型。

单任务单写者；本阶段不用隐式 list append reducer。节点返回替换字段；observation_ids 按稳定 ID 去重，当前布局/未知/视角/验证 ID 集合在 belief 更新时清空并重新计算，历史仍在 DB。不得用旧 selected_view 或旧 verification 执行。checkpoint 不保存 RGB、Depth、无限聊天或完整历史快照。

## 核心模型

| 模型 | 合同 |
| --- | --- |
| Observation | task、实际 camera_view、pose、时间、环境版本、artifact 哈希、calibration、operation 来源 |
| CameraPose | 米，右手世界坐标 X/Y 水平、Z 向上；xyzw 单位四元数，表示局部到 frame_id 的旋转 |
| SceneBelief | belief_id/version、父版本、room geometry、objects、regions、claims、observation IDs、fusion 版本 |
| SceneObject | 语义、定向包围盒、存在概率、知识状态、独立不确定特征与证据；未测几何用 None |
| CandidateLayout | 绑定 belief 版本，目标 placements、硬软约束、required_assumptions、预期分数与可行度 |
| LayoutAssumption | 稳定 assumption_id → claim_id → expected_value/confidence/evidence |
| CriticalUnknown | claim/assumptions/affected layouts、至少两个可能结果，每个结果对可行性、排序/分数/动作的影响 |
| CandidateView | 位姿、目标 unknown IDs、信息收益/决策相关性/可见性/移动成本/冗余、可达状态及评分配置 |
| VerificationResult | 规则、布局、belief/env 版本、四态结果、度量/阈值/单位、对象、证据、规则版本 |
| ActionResult | operation/decision/layout/verification 关联、前后版本、succeeded/failed/partial/unknown |

Camera 局部坐标约定 +X 向右、+Y 向上、-Z 为观察方向；adapter 负责与渲染器/传感器轴向转换，深度格式通过 calibration/artifact 元数据说明。geometry 的 Box 使用同一姿态类型，仅表示一般刚体位姿。跨 frame 的观测必须经过版本化标定，不能直接融合。

## 跨实体不变量

由未来 application/repository 边界校验：所有引用同 task/project；parent version 单调；同 belief 内 object/region/claim ID 唯一；assumption claim 存在；unknown 的 effects 只引用当前布局且覆盖受影响布局；至少两种结果产生有意义的决策差异；view 只引用当前 open unknown；被选择视角必须已评分、可达、符合预算；执行 decision/layout/belief/verification 版本一致。

supported/refuted claim 必须有证据；unobserved 可无证据。independent observation_count 根据不同有效视角计算，不按图片数量计算。free region 必须有空间证据。room_geometry.complete 默认 false，列表为空不代表没有墙。字段级验证已实现，跨实体与研究语义验证留给后续服务，不能宣称当前模型已保证全部不变量。

Uncertainty aggregate 及 criticality=uncertainty×impact 仅为版本化策略输出；未知值 None 不得当 0。原文视角评分五项保留，movement_cost 进入评分前归一化并记录尺度；预算实际旅行距离以米计算。权重与阈值在实验配置冻结，本阶段不实现评分。

## V0 实现状态

checkpoint 使用 BeliefRefData、BudgetData、DecisionData TypedDict JSON 对象；领域模型在节点读取时 Pydantic 重建。新增 observation_round 和 final_result，结果带 mode=fake-v0。每次节点结果提交前通过 RoomScoutState TypeAdapter 校验。operations 保存该节点 patch；checkpoint 落后时复用已提交 patch。Graph 的 10 个要求节点全部注册，另含 validate/blocked。

## SceneBeliefService 模块增量

Observation 增加 observed_regions、observed_claims、observed_room_geometry 可选结构化字段，旧 schema v1 默认空/None，详见 ADR 0003。SceneBelief 与 RoomScoutState 公共字段未变。scene-belief-v1 配置下 belief.fusion_version 绑定真实服务算法及配置哈希；其他 V0 阶段仍 Mock。行为与限制见 [模块文档](SCENE_BELIEF_SERVICE.md)。
