# ADR 0006 — 场景几何诊断与受控位置修正

依据用户本轮请求及《Verify, Diagnose, Repair》六页提案，实现独立第一阶段子系统，不改变 RoomScout 的部分观测研究主链。附件的实验设想不等于本轮要求完成 VLM 对照实验或 Physics Verifier。

冻结 GeometryVerifier.verify(task,belief,layout,geometry) 面向候选布局。新增实现类同时提供 diagnose(snapshot)；以新增模块内 SceneSnapshot/DiagnosisReport 表达完整场景诊断，不修改已有公共模型。verify 将候选位置应用到输入快照后转换成原 VerificationResult。SceneSnapshot 通过 geometry artifact 提供，不能从 Belief 不完整对象集推断全场景无碰撞。

范围：米、右手 Z-up、盒体 JSON；OBB SAT 检测穿透和最小分离平移；轴对齐盒体报告精确交集体积，旋转盒体体积 unknown。支撑首版只验证轴对齐实体盒、水平顶面/地面、显式 support_id、接触面积与重心投影 margin；缺失重心明确假设均匀实体盒。未指定 support_id 的物体报告 unknown，不猜测杯子应在地面还是桌面；倾斜或旋转支撑返回 unknown。支撑链必须最终接地或 anchored，悬空支撑物不能使上层对象通过。几何通过不等于动力学稳定。

报告包含逐规则 pass/fail/unknown、对象 ID、量纲、阈值、可编辑变量、修正提示、场景/配置哈希。容差配置化。接触不是穿透。对象固定属性与是否需要支撑独立；不能通过把物体标 fixed 假装接地。

修正使用独立受限 MOVE policy（Rule-Repair 基线，非 LLM），消费诊断中的提示，逐候选全场景复查，只接受失败/未知诊断目标改善且不产生新失败或未知的变更。有限轮数、单步位移限制；无法改善返回 blocked。禁止删除、缩放、改支撑对象或偷偷解锁固定对象。记录原始场景、报告、提案、复查和实际接受的动作；输出新 JSON，不覆盖用户原文件，不执行真实设备。

本轮不实现 mesh 精确窄相碰撞、多接触刚体平衡、摩擦/质量动力学、房间导航、LLM 修正或论文效果实验。既有 Planning/Memory/LangGraph 不修改。
