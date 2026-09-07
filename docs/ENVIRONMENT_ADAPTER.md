# 最小仿真 EnvironmentAdapter

实现 `app/environment/minimal.py:MinimalEnvironmentAdapter`，符合既有 async EnvironmentAdapter 方法签名。Planning、Memory、LangGraph 和公共数据模型均未修改。决策记录见 ADR 0004。

## 已实现

- `observe(ctx)`：解析式 OBB 射线投射，真实遮挡、RGB PNG、Depth JSON、相机内参 artifact；返回实际 CameraPose 和 camera_view_id。
- `move_camera(pose,ctx)`：校验 frame、房间边界和相机直线路径，碰撞失败保持旧位姿；原地旋转可用。
- `get_depth(observation)`：读取历史观测对应 Depth 引用并校验归属及内容哈希，与当前相机位置无关。
- `detect_collision(layout,ctx)`：对布局替换后的盒体做 15 轴 OBB SAT，检测目标物体与环境/其他目标物体重叠及越界。接触在配置容差内不算穿透。
- `measure_geometry(query,ctx)`：支持已提交 layout 的完整 collision 查询（object_ids/region_ids 为空）；未登记 layout 返回 unknown，其他查询 unsupported。
- `reconcile(ctx)`：持久操作收据查询。没有修改空间布局的能力，`execute_layout` 明确抛 UnsupportedCapability。

## Demo

```sh
.venv/bin/python -m scripts.demo_environment
```

或容器：

```sh
docker compose up -d --build
docker compose exec -T api python -m scripts.demo_environment --output /tmp/environment-demo
```

Demo 依次采集初始视角、侧移相机、再次采集、提交一个与暖气片重叠的演示布局查询。输出 JSON 内提供 RGB/Depth/calibration/collision artifact URI，产物位于指定目录。重复运行相同目录复用操作结果；新实验使用新目录。Demo 不接到现有 Graph，不调用 Planning/Memory，不执行布局。

## 数据合同

默认房间 6×6×3 米，桌子、屏风、屏风后暖气片；配置在 configs/minimal-room.json。环境版本绑定完整配置 SHA-256。世界坐标右手 Z-up；相机局部 +X 右、+Y 上、-Z 前，xyzw 单位四元数表示相机到世界旋转。

RGB 为 8-bit RGB PNG、平涂颜色。Depth media_type 为 application/vnd.roomscout.depth+json，depth_m 为 height×width 行优先二维数组，单位米，表示相机光轴深度；无命中/超裁剪为 null，不使用零深度代表空闲。方形像素，fx=fy，内参给出 cx/cy、近远裁剪与像素中心约定。默认 96×72；未包含纹理、阴影、传感器噪声或真实感材质。

Observation 不返回全量 scene_objects、房间结构、分割或被遮挡物体检测。RGB/Depth 是本轮真实计算结果，但图像到 SceneBelief 的感知适配仍未实现，不能将仿真真值对象列表当观察注入。

Collision GeometryEvidence 携带 application/vnd.roomscout.collision+json；CollisionReport 包含 layout_id、revision、collision、pairs、outside_room_ids、tolerance_m、geometry_source=simulator_ground_truth。查询结果仅供确定性验证侧使用，不是额外感知证据；未实现门开启净空、可达路径或动力学验证。

## 持久化与恢复

调用方负责 PostgreSQL TOOL_CALL 提交后调用 Adapter，结果提交后继续处理。Adapter 局部会话账本不替代 Event Log。task 隔离的文件目录保存姿态、逻辑时钟、操作指纹和结果；锁内原子替换、fsync；artifact 先提交再提交 observation 收据。崩溃留下的未引用 artifact 可保留，不影响重放；同 ID 异参、旧环境版本、旧 fencing token 均拒绝。

相机移动不改变场景几何版本。观测时间是固定实验 epoch 加逻辑 tick，明确为仿真时间。session 文件要求本地支持 flock/atomic rename/fsync 的文件系统；不宣称支持多主机分布式文件锁。测试和 Demo 的任务身份由构造器绑定。

## 测试

```sh
.venv/bin/python -m pytest tests/test_minimal_environment.py -q
./scripts/test-compose.sh
```

测试覆盖中心像素解析深度、光轴深度与距离区别、RGB 文件与视角变化、裁剪、遮挡、路径失败、旋转、重启幂等、fencing、任务/环境隔离、碰撞/接触/越界、artifact 完整性，以及 PostgreSQL Event First 与观测 round-trip。全部原有测试继续回归。

### 验收记录（2026-09-07）

- 本地启用 PostgreSQL integration 的完整测试：80 passed（新增 14 项）。
- Docker Compose 容器完整测试：80 passed；存在一条上游 Starlette/AnyIO 弃用警告。
- 本地与容器 Demo 均运行成功；初始与侧移后的 RGB 已人工查看，遮挡随视角变化；演示 collision report 为 collision=true、pairs=[["desk","radiator"]]。
- API/DB 容器健康；本模块通过独立 Demo 和持久事件集成测试验收，现有 Graph 的 Fake Environment 尚未替换。
