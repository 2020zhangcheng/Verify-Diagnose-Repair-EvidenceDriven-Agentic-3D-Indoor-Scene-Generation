# ADR 0003 SceneBeliefService 首版融合

2026-09-07，实施。范围仅限 SceneBeliefService 及必要观测合同、节点接线和测试。不实现感知、布局、Critical Unknown、View Utility 或 GeometryVerifier 算法。

## 接口变更先行说明

原 Observation 只有 detected_objects，无法携带显式空间测量；否则服务只能从“没有检测到对象”臆造空闲区域。增加 observed_regions、observed_claims（默认空 tuple）、observed_room_geometry（默认 None）。复用既有 SceneRegion/Claim/RoomGeometry 类型，SceneBeliefService.update(previous, observations, task) 签名和 SceneBelief schema 不变。

这是向后兼容的 schema v1 可选字段扩展：历史 event 可读取，新字段缺失不等于观测到空闲。旧严格客户端需升级模型后才能读取新演示数据；fake-v0 继续保留。事件及实体幂等比较需规范化旧可选字段，不能修改原历史行。新增 HTTP config_id=scene-belief-v1，仅选择本模块的真实服务；其余模块仍 Mock。

## 融合策略与可复现边界

- 服务不读写数据库或记忆。历史 observation loader 由构造器注入，节点加载已持久化观测。incremental update 通过 previous.observation_ids 读回不可变历史，按时间/ID 稳定重算；不把旧融合均值再当新证据。无 loader 时仅支持 previous=None 的完整批次；缺失历史失败，不能静默丢弃旧证据。
- 相同 observation ID 不得对应不同内容；重放相同观测集合返回原 belief，不增加版本；新增证据形成新版本与 parent。视角数按位置/朝向阈值聚类，不按图片数或任意 camera_view_id 计数。阈值聚类是工程近似，不声称统计独立性。
- 第一版要求同 task、同 world frame、同 environment_revision；跨坐标系需要 adapter 先转换，环境发生动作变化后需要新 epoch，从 previous=None 开始。拒绝合并旧环境几何。稳定 object/region/claim ID 由上游提供，本模块不实现数据关联。
- 配置包含全部融合权重、几何/视角阈值。fusion_version 包含算法版本及配置 SHA-256；event 中的 BELIEF_UPDATED 保存该标识，TOOL_CALL 保存实际配置，支持录制观测重放。
- 相容几何按每个视角组代表样本加权融合；超过位置/尺寸/角度阈值或语义不一致时保留代表估计，显式提高 uncertainty 和 cross_view_inconsistency，保留所有来源证据。首版冲突不自动消除，原始冲突假设通过 Observation 事件追溯。
- 未检测到对象不删除旧对象。未观察/遮挡区域不能变成 free。free 需要显式区域观测及可见性、遮挡、几何质量门槛；free/occupied 冲突降为 uncertain。is_empty Claim 的支持必须与区域证据一致。
- Uncertainty 原始缺失值仍为 None；aggregate 对启用但缺失的特征使用配置的保守风险值，不伪造已知值。权重仅用于可解释风险评分，不表示校准后的概率。visibility 取已观测最大值，是覆盖下界近似，不累加像素覆盖。
- 房间结构仅合并显式输入的表面盒体并做相同几何去重；不推断墙体、做 SLAM 或平面融合；不推断 complete。原始对象/区域/Claim 的证据在融合时绑定其承载 Observation，拒绝跨观测、跨 camera 或不存在 artifact 的引用。

## 接入范围

新增 app/scene 内服务、配置、演示输入、节点 wrapper；对 V0 node 只增加 scene-belief-v1 分支。此分支的 Observation 仍为脚本 fixture，但 SceneBelief 必须由真实服务根据输入生成，不再根据观察轮数写死。原 fake-v0 的分支和研究外模块行为不变。服务的 DB 集成、checkpoint 重放及 V0 回归均需测试。
