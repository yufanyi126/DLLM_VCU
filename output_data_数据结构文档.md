# `output_data` JSON 数据结构文档

> 本文档描述 `main.py` 中 `step()` 函数返回的 `output_data` JSON 结构。
> 该数据通过 `car_server.py` 经 WebSocket 推送给 `receive_demo.py` 进行 AI 决策。

---

## 顶层结构

```json
{
  "route_summary": { ... },
  "segments": [ ... ]
}
```

---

## 1. `route_summary` — 路线概要

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `total_distance_km` | `float` | 高德 API | 路线规划总距离（公里），初始化时由 `RouteTracker.original_total_distance` 计算，`sum(所有路段 distance) / 1000` |
| `remaining_km` | `float` | 计算得出 | 剩余距离（公里），`max(0, 总距离 - 累计行驶距离) / 1000` |
| `progress_pct` | `float` | 计算得出 | 行程进度百分比，范围 `0.0 ~ 100.0`，使用高水位标记确保单调递增不倒退 |
| `accumulated_dist_m` | `float` | 计算得出 | 已累计行驶距离（米），`RouteTracker.cumulative_distance` 的高水位值 |
| `current_segment` | `int` 或 `null` | 计算得出 | 当前所在路段序号，**从 1 开始计数**；未定位或已到达终点时为 `null` |

---

## 2. `segments[]` — 全路段状态列表

每个元素为一个路段对象，包含**静态字段**（高德 API 初始化时填充）和**运行时字段**（CAN/GPS 实时更新）。

### 2.1 静态字段（来自高德 API，全程不变）

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `sequence` | `int` | 高德 API / 本地编排 | 路段序号，从 `1` 开始，对应 `step_index + 1` |
| `route` | `str` | 高德 API | 道路名称，取值来自 `step.road_name`；若为空则用 `step.instruction` 替代。示例：`"金开大道"`、`"内环快速"` |
| `instruction` | `str` | 高德 API | 导航指令文本。示例：`"沿金开大道向南行驶"`、`"左转进入黄山大道"`、`"到达终点"` |
| `distance_m` | `float` | 高德 API | 路段全长（米），来自 `step.step_distance` |

### 2.2 动态字段（随 GPS/CAN 实时更新）

| 字段 | 类型 | 来源 | 说明                                                                                                                                                                                                                                                                   |
|------|------|------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `remaining_distance_m` | `float` | 计算得出 | 该路段剩余距离（米）。`"completed"` 路段恒为 `0`；`"pending"` 路段恒为 `distance_m`；`"current"` 路段为 `max(0, distance_m - 已行驶距离)`                                                                                                                                                         |
| `traffic` | `str` | 高德 API（路况刷新） | 当前路况状态。可能值：<br>• `"畅通"` — 道路畅通<br>• `"缓行"` — 行驶缓慢<br>• `"拥堵"` — 交通拥堵<br>• `"严重拥堵"` — 严重拥堵<br>• `"未知"` — 无数据或未获取到路况                                                                                                                                                   |
| `status` | `str` | 逻辑判断 | 路段行驶状态。可能值：<br>• `"pending"` — 尚未到达该路段<br>• `"current"` — 当前正在行驶的路段<br>• `"completed"` — 已走过的路段                                                                                                                                                                      |
| `speed` | `float` 或 `null` | CAN / GPS / 模拟 | 当前车速（km/h），精确到 1 位小数（如 `48.5`）。优先级：CAN 车速 > GPS 地速 > 模拟车速 > `0`。`"pending"` 路段为 `null`                                                                                                                                                                               |
| `speed_source` | `str` 或 `null` | 逻辑判断 | 车速数据来源。可能值：<br>• `"can"` — CAN 总线车速（`VITESSE_VEHICULE_ROUES`）<br>• `"gps"` — GPS 地速（`GroundSpeed`）<br>• `"simulated"` — 强制模拟车速（`USE_SIMULATED_SPEED=True`）<br>• `"simulated_fallback"` — GPS+CAN 均无效时的兜底模拟车速<br>• `"none"` — 无可用速度数据，取 `0`<br>`"pending"` 路段为 `null` |
| `can_speed` | `float` 或 `null` | CAN 信号 | CAN 总线原始车速（km/h），信号名 `VITESSE_VEHICULE_ROUES`。`"pending"` 路段为 `null`                                                                                                                                                                                                 |
| `driving` | `str` | CAN 信号 | 驾驶模式，信号名 `STDE_DRV_DYN_MODE_STATE`。映射关系：<br>• `0` → `"ECO"` — 经济模式<br>• `1` → `"标准"` — 标准模式<br>• `2` → `"SPORT"` — 运动模式<br>• `3` → `"自定义"` — 自定义模式<br>• 未知值 → `"未知(N)"`<br>• CAN 无数据 / `"pending"` 路段 → `""` 空字符串                                                    |
| `engy_mode` | `str` | CAN 信号 | 能量模式，信号名 `STDE_DRV_ENGY_MODE_STATE`。映射关系：<br>• `0` → `"EV"` — 纯电模式<br>• `1` → `"HEV"` — 混合动力模式<br>• 未知值 → `"未知(N)"`<br>• CAN 无数据 / `"pending"` 路段 → `""` 空字符串                                                                                                        |
| `soc` | `str` | CAN 信号 | 高压电池剩余电量百分比，信号名 `HV_BATT_SOC`。格式为 `"XX.X%"`（如 `"78.5%"`）。CAN 无数据 / `"pending"` 路段为 `""`                                                                                                                                                                              |
| `gear` | `str` | CAN 信号 | 当前档位，信号名 `POS_MONOSTABLE_LEVER`。映射关系：<br>• `0` → `"P"` — 驻车档<br>• `1` → `"R"` — 倒车档<br>• `2` → `"N"` — 空档<br>• `3` → `"D"` — 前进档<br>• 未知值 → `"未知(N)"`<br>• CAN 无数据 / `"pending"` 路段 → `""` 空字符串                                                                      |
| `throttle` | `str` | CAN 信号 | 油门踏板开度，信号名 `EFCMNT_PDLE_ACCEL_228` 或 `EFCMNT_PDLE_ACCEL_278`。格式为 `"XX.X%"`（如 `"25.0%"`）。CAN 无数据 / `"pending"` 路段为 `""`                                                                                                                                               |
| `battery` | `str` | CAN 信号 | 高压电池平均温度，信号名 `HV_BATT_TEMP_AVG`。格式为 `"XX°C"`（如 `"35°C"`）。CAN 无数据 / `"pending"` 路段为 `""`                                                                                                                                                                              |
| `d0_status` | `str` | GPS 传感器 | 惯导 D0 收敛状态，来自 GPS 模块的 `D0Status` 字段。可能值：`"0"`、`"1"`、`"2"`、`"3"` 等，数字越大代表收敛程度越高，离线情况下定位能力更强，无数据时为 `"N/A"`                                                                                                                                                             |

---

## 3. 路段状态与字段填充规则

| 路段状态 | `status` | 运行时字段 (`speed`, `driving`, `engy_mode`, `soc` 等) | `remaining_distance_m` | `traffic` |
|----------|----------|----------------------------------------------------------|------------------------|-----------|
| 未到达 | `"pending"` | 全部为空/默认值（`null` / `""` / `"N/A"`） | = `distance_m`（全长） | API 返回的路况 |
| 当前 | `"current"` | **填充实时 CAN/GPS 数据** | = `max(0, distance_m - 已行驶)` | API 刷新值 |
| 已走过 | `"completed"` | 保留最后一次采集的快照 | = `0` | API 返回的路况 |

---

## 4. 完整 JSON 示例

```json
{
  "route_summary": {
    "total_distance_km": 5.23,
    "remaining_km": 3.10,
    "progress_pct": 40.7,
    "accumulated_dist_m": 2130.5,
    "current_segment": 3
  },
  "segments": [
    {
      "sequence": 1,
      "route": "黄山大道",
      "instruction": "沿黄山大道向西行驶",
      "distance_m": 1200.0,
      "remaining_distance_m": 0.0,
      "traffic": "畅通",
      "status": "completed",
      "speed": 45.0,
      "speed_source": "gps",
      "can_speed": 44.5,
      "driving": "ECO",
      "engy_mode": "EV",
      "soc": "85.2%",
      "gear": "D",
      "throttle": "20.0%",
      "battery": "30°C",
      "d0_status": "0"
    },
    {
      "sequence": 2,
      "route": "金开大道",
      "instruction": "沿金开大道向南行驶",
      "distance_m": 2500.0,
      "remaining_distance_m": 0.0,
      "traffic": "缓行",
      "status": "completed",
      "speed": 35.2,
      "speed_source": "can",
      "can_speed": 35.0,
      "driving": "ECO",
      "engy_mode": "EV",
      "soc": "72.5%",
      "gear": "D",
      "throttle": "20.1%",
      "battery": "32°C",
      "d0_status": "0"
    },
    {
      "sequence": 3,
      "route": "金渝大道",
      "instruction": "沿金渝大道向东行驶",
      "distance_m": 1800.0,
      "remaining_distance_m": 870.5,
      "traffic": "拥堵",
      "status": "current",
      "speed": 25.8,
      "speed_source": "can",
      "can_speed": 26.0,
      "driving": "标准",
      "engy_mode": "EV",
      "soc": "68.3%",
      "gear": "D",
      "throttle": "15.5%",
      "battery": "33°C",
      "d0_status": "0"
    },
    {
      "sequence": 4,
      "route": "龙山路",
      "instruction": "沿龙山路向北行驶",
      "distance_m": 900.0,
      "remaining_distance_m": 900.0,
      "traffic": "畅通",
      "status": "pending",
      "speed": null,
      "speed_source": null,
      "can_speed": null,
      "driving": "",
      "engy_mode": "",
      "soc": "",
      "gear": "",
      "throttle": "",
      "battery": "",
      "d0_status": "N/A"
    }
  ]
}
```


