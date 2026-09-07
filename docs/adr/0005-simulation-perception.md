# ADR 0005 — 可见性约束的仿真 GT 感知

用户授权使用仿真 Ground Truth 构造感知结果。本轮保留 Observation、SceneBeliefService 及 Planning/Memory/Graph 公共协议，复用 StructuredSceneBeliefService。

新增独立 SimulationPerception：从原始 Observation 的相机位姿和绑定版本的静态场景计算逐像素对象命中，与房间表面一起做遮挡判定。只有实际可见像素的对象输出；ID 与完整 OBB 来自 oracle，不声称从 RGB/Depth 重建完整几何。visibility 定义为视锥内无遮挡对象投影像素中实际可见比例，occlusion=1-visibility，不代表表面覆盖率。

感知结果使用新 Observation ID，原始观测保持不可变。JSON artifact 记录原始观测、场景版本、配置、像素计数和 GT 来源；Evidence 指向新观测及 artifact。调用方必须先持久化 TOOL_CALL，再处理、持久化 Observation，随后融合 Belief。

置信度是配置化实验代理量，非校准概率。沿用 Belief 的历史最佳 visibility/最低 occlusion，不能解释为当前视角可见性；逐视角数值保留在 Observation。未看到的对象不删除，不凭未命中断言自由空间。本轮不生成房间/区域真值，不实现动态场景或真实数据关联。
