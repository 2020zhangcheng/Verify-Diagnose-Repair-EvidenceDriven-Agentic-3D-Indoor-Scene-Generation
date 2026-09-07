# ADR 0004 最小仿真 EnvironmentAdapter

2026-09-07。保持全部公共 Protocol 不变；只新增 environment 模块、独立配置、Demo 和测试。不修改 Planning、Memory、LangGraph 或 SceneBelief 算法。

使用纯 Python 解析式盒体仿真器作为首个可运行后端，而非提前引入 Blender/Habitat 安装依赖。支持 OBB 射线求交、针孔 RGB PNG、相机光轴深度 JSON、相机姿态与内参、OBB SAT 碰撞/房间边界查询。没有渲染器脚本冒充传感器结果，不实现布局执行。

GeometryEvidence 已允许 artifact 引用，碰撞报告作为结构化 JSON artifact 返回，无需更改公共模型。碰撞查询明确使用 simulator_ground_truth，仅供几何验证；observe 只返回 RGB/Depth/calibration，不输出被遮挡实体列表或房间真值给规划。场景全量配置是仿真/实验输入，不是 Agent 观测。

坐标沿用米、右手 Z-up、相机局部 +X 右/+Y 上/-Z 前、xyzw 四元数。Depth 是 camera_z（非欧氏距离），无命中为 null，近远裁剪同单位。移动保持场景 environment_revision 不变；实际 camera_view_id 改变。点相机加配置半径，沿直线路径检查环境障碍，失败不更新位姿。

本地文件会话按 task 隔离，通过文件锁、原子替换和 fsync 持久化 pose/operation receipt；同 operation ID 同参数复用结果，异参拒绝，低 fencing token 拒绝。进程重启后恢复会话及产物。此局部账本负责 adapter 幂等，不替代业务 PostgreSQL Event First；Demo 提供独立数据库集成测试，Graph 接线留待用户指定。

RGB 为简单物体颜色，无纹理/光照物理；Depth 与碰撞计算真实运行，非硬编码。execute_layout 明确抛 UnsupportedCapability，不伪造成功。measure_geometry 仅支持已经提交过的 layout 的 collision 查询，其他规则 unsupported。没有真实传感器、动力学、导航规划、检测/分割、VLM 或布局执行。
