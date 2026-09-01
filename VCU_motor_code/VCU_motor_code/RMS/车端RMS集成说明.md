# 车端 RMS 驾驶画像模块 · 集成说明

> 适用范围：`DLLM_VCU_V3.2` 车端主程序（`main.py` / `RMS/rms_call.py`）中的驾驶画像计算链路。
> 底层模型：`RMS/portrait_core.cp311-win_amd64.pyd`（Windows 64 位 / Python 3.11），详见同目录 `接口说明书.md`。
> 本文档描述**当前代码实际使用方式**，与模型原始说明书存在裁剪差异，以本文档为准。

---

## 1. 模块概述与数据链路

RMS（Remote Monitoring System）驾驶画像模块对一段连续行驶数据（≥290 秒）计算**驾驶风格**与**行驶工况**两类画像，结果随 `output_data` 发给 AI 上位机，作为决策 Agent 的 `driver_style` / `driving_condition` 输入。

数据链路（与数据源无关，仿真/实车通用）：

```
CAN 信号（Simulink 或真实 CAN 盒）
   │  main.py step()
   ▼
_rms_sample  {ts, speed, voltage, current, soc, accel_pedal, gear, temp_max}
   │  _update_rms_buffer()：每 10s 降采样 1 帧
   ▼
csv_segment_buffer（按真实时间跨度累积 ≥310s 切段）
   │  _flush_current_segment()
   ├──► _export_rms_window_csv()  →  RMS/captured/ 调试 CSV（可选，受 RMS_EXPORT_CSV 控制）
   └──► _submit_rms_async()       →  后台线程池（rms_worker，1 线程）
                                        │
                                        ▼
                              rms_call.analyze_df(df, platform='BEV', capacity=60)
                                        │
                                        ▼
                              session['rms_result'] = {style, scenario, score, confidence, updated_at}
                                        │
                                        ▼
                              output_data["rms"] = {style, scenario}
                                        │
                                        ▼
                              car_server.py → WebSocket → receive_demo.py → AI 决策
```

**关键点**：RMS 计算直接吃内存 `DataFrame`，**不需要导出 CSV**。CSV 保存只是调试功能，方便查看数据，可在Main.py 的84行进行开关设置。

---

## 2. 输入数据要求

### 2.1 喂给模型的字段（当前代码固定 8 列）

`main.py::_build_rms_df()` 按固定列序构造：

| 列名 | 单位/取值 | 来源信号（can_signals） | 说明 |
|---|---|---|---|
| `ts` | datetime | 本帧壁钟时间 `time.time()` | 采样时间戳 |
| `speed` | km/h | 速度优先级：CAN 车速 > GPS 地速 > 0 | 参与模型计算的"极限可算字段" |
| `voltage` | V | `HV_BATT_REAL_VOLT_HD`（0x4D8） | 动力电池总电压 |
| `current` | A | `HV_BATT_REAL_CURR_HD`（0x4D8） | 总电流，**放电为正、回收为负** |
| `soc` | % | `HV_BATT_SOC`（0x5A2） | 0~100 百分数（已归一化） |
| `accel_pedal` | % | `EFCMNT_PDLE_ACCEL_228`/`_278`（0x228/0x278） | 加速踏板行程 |
| `temp_max` | ℃ | `HV_BATT_TEMP_MAX`（0x5A2） | 电池最高温度 |

> 注：实际传给模型的 DataFrame 含 `gear` 列（恒 0）；CSV 落盘时 `_format_rms_csv_df()` 会 drop 掉 `gear`，落盘列序为 `ts, speed, voltage, current, soc, accel_pedal, temp_max`。

### 2.2 物理量范围过滤（`_sanitize` 兜底）

`step()` 组装样本时做范围校验，**越界值一律回退为 0**，防止脏数据污染画像：

| 字段 | 合法范围 | 越界处理 |
|---|---|---|
| speed | -50 ~ 300 km/h | 置 0 |
| voltage | 0 ~ 2000 V | 置 0 |
| current | -2000 ~ 2000 A | 置 0 |
| soc | 0 ~ 100 % | 置 0 |
| accel_pedal | 0 ~ 100 % | 置 0 |
| temp_max | -50 ~ 150 ℃ | 置 0 |

模型侧另有二次校验（越界置空、核心字段越界帧 >10% 判 `FIELD_OUT_OF_PHYSICAL_RANGE`），两端共同兜底。

### 2.3 采样与窗口（必须满足模型硬性约束）

| 项 | 当前代码配置 | 模型硬性要求 | 结论 |
|---|---|---|---|
| 有效帧数 | 310s ÷ 10s ≈ 31 帧 | ≥ 25 帧 | ✅ 满足 |
| 窗口时长 | 目标 310s（`CSV_SEG_TARGET_SEC`） | ≥ 290 秒 | ✅ 留 20s 余量 |
| 采样间隔 | 10s（`CSV_SAMPLE_GAP_SEC`） | 中位数 ≤ 60s | ✅ 满足 |
| 片段性质 | 行驶（放电）片段 | 不含充电（SOC 不升） | 

切段策略（`_maybe_flush_segment_csv`）：
- 缓冲累积到「末帧 ts − 首帧 ts ≥ 310s」→ 落盘为一段并触发一次 RMS；
- 程序退出强制 flush 时：末段 ≥ 290s 独立成段；< 290s 且存在上一段文件时**合并进上一段**，避免生成不达标短文件。

### 2.4 上游预处理（进入样本前）

- **垫值**：`HV_BATT_REAL_CURR_HD` / `HV_BATT_REAL_VOLT_HD` / `HV_BATT_TEMP_MAX` / `HV_BATT_SOC` 缺失时用 `_SIM_BAT_*_POOL` 随机池静默填充（仿真与实车模式都会执行，实车联调需警惕——见 §6）；
- **归一化**：`_to_percent100()` 将 0~1 比例信号转为 0~100 百分数（`HV_BATT_SOC`、`EFCMNT_PDLE_ACCEL_228`），已是百分数（>1）则保持不变；
- **取整**：落盘 CSV 时 `soc`、`accel_pedal` 用 `_round_half_up()` 四舍五入（非截断）。

---

## 3. 输出数据解释

### 3.1 `RMS/rms_call.py::analyze_df()` 返回（精简画像）

```python
{
    'ok': True,
    'style': 'normal',              # 驾驶风格
    'score': 42,                    # 激进度评分 0~100
    'confidence': 0.87,             # 风格分类置信度 0~1
    'scenario': 'urban_smooth',     # 行驶工况
    'congestion': 'light',          # 拥堵等级
    'distance_km': 3.2,             # 窗口行驶距离
    'quality': 0.95,                # 数据质量分 0~1
    'warnings': [...],              # 数据可改进点提示
}
```

`ok=False` 时返回 `{'ok': False, 'code': '<abnormal_code>', 'reason': '中文原因'}`。

### 3.2 `main.py::_run_rms_on_df()` 精简后写入 `session['rms_result']`

只保留 5 个字段（供 AI 消费与调试）：

```python
{
    'style': 'normal',          # ← driving_style_profile.driving_style
    'scenario': 'urban_smooth', # ← driving_scenario_profile.driving_scenario
    'score': 42,                # ← driving_style_profile.aggressiveness_score
    'confidence': 0.87,         # ← driving_style_profile.style_confidence
    'updated_at': 1690000000.0, # 计算完成壁钟时间戳
}
```

**异常/失败时返回 `None`，`session['rms_result']` 保留上一次有效值**，不覆盖、不阻塞实时回路。

### 3.3 `output_data["rms"]`（发给 AI 上位机的字段）

```python
output_data["rms"] = {"style": "normal", "scenario": "urban_smooth"}
```

`receive_demo.py` 消费时的词汇表与归一化：

| RMS 原始值 | AI 侧归一化 | 说明 |
|---|---|---|
| `style`: `very_aggressive` | → `aggressive` | `rule_decide` 无 very_aggressive 分支 |
| `scenario`: `elevated_cruise` | → `highway_cruise` | 归一以命中规则 |
| `scenario`: `urban_congested` | → `urban_smooth` | 同上 |
| 其余 | 原样使用 | 必须 ∈ 枚举集，否则回退推断 |

- `driver_style` = `rms.style`（在枚举 `{conservative, normal, aggressive, very_aggressive}` 内才用，否则默认 `normal`）；
- `driving_condition` = `rms.scenario`（在枚举内才用，否则按路名/交通文本推断）。

---

## 4. 异常识别（`meta.status == 'abnormal'`）

模型对不可信输入不输出画像，返回异常码。当前代码处理方式：`ok=False` → 返回 `None` → 保留上一次画像 → AI 收到旧值/None，不影响实时链路。

| 层 | 异常码 | 触发条件（与当前配置的关系） |
|---|---|---|
| 结构 | `TOO_FEW_FRAMES` | 有效帧 < 25（当前 310s/10s≈31 帧，一般不会触发） |
| | `WINDOW_TOO_SHORT` | 时长 < 290s（切段策略已规避） |
| | `SAMPLING_TOO_SPARSE` | 采样中位数 > 60s（当前 10s，不会触发） |
| | `MISSING_REQUIRED_COLUMN` | 缺 `ts/speed/current/voltage/soc` 之一 |
| | `INVALID_TIMESTAMP` | 时间戳无法解析 |
| 字段 | `CORE_FIELD_MOSTLY_INVALID` | 核心字段有效率 < 70% |
| | `FIELD_OUT_OF_PHYSICAL_RANGE` | 核心字段越界帧 >10%（多为单位错误） |
| 物理 | `SOC_RISE_IN_DISCHARGE` | SOC 上升 >2%（含充电片段）⚠️ 最常见 |
| | `NO_MOVEMENT` | 窗口内几乎无行驶 |
| | `ENERGY_DIRECTION_CONFLICT` | 行驶中净能量为负 |
| | `ACC_EXCEEDS_PHYSICAL_LIMIT` | >2% 帧加速度超 4 m/s² |
| | `POWER_OUT_OF_RANGE` | P95 瞬时功率 > 400 kW |
| | `ENERGY_OUT_OF_RANGE` | 百公里能耗超物理范围 |
| | `SPEED_SIGNAL_STUCK` | 整段车速取值数 ≤2 |
| 分布 | `DISTRIBUTION_OUTLIER` | 特征组合统计异常 |

---

## 5. 相关配置开关（`main.py` 顶部）

| 配置项 | 默认值 | 作用                                                |
|---|---|---------------------------------------------------|
| `RMS_ENABLED` | `True` | `False` 时完全不跑 RMS，AI 收到 `rms=None`(main.py 的90行)  |
| `RMS_EXPORT_CSV` | `True` | 是否把段窗口保存到 `RMS/captured/`（仅调试，不影响画像，main.py 的84行） |
| `RMS_EXPORT_CSV_DIR` | `RMS/captured` | CSV 保存目录                                          |
| `CSV_SAMPLE_GAP_SEC` | `10` | 源头降采样间隔（秒/帧）                                      |
| `CSV_SEG_TARGET_SEC` | `310` | 切段目标跨度，留 20s 余量满足 290s 硬约束                        |
| `CSV_SEG_MIN_SEC` | `290` | 末段最低独立成段跨度，不足则并入上一段                               |
| `RMS` 调用参数 | `platform='BEV'`, `capacity=60` | 在 `main.py::_run_rms_on_df()` 中写死                 |

---

## 6. 常见问题

**Q1：AI 收到的 `rms` 一直是旧值/None？**
`ok=False` 时保留上次画像。排查控制台 `[RMS]` 日志：`分析未通过（跳过，AI 取 None）` 会打印原因；`[RMS] 后台任务异常` 为程序异常。

**Q2：为什么经常出现 `SOC_RISE_IN_DISCHARGE`？**
片段内能量回收/充电导致 SOC 上升 >2% 即被判定。当前切段按固定 310s 跨切，无法感知充电段。若实车频繁触发，需要在上游把片段切分改为"按 SOC 单调下降区间"切段。

**Q3：实车模式下电压/电流/温度是随机值？**
`step()` 的垫值逻辑在实车模式下也会执行：只要 `can_signals` 缺 `HV_BATT_REAL_CURR_HD`/`HV_BATT_REAL_VOLT_HD`/`HV_BATT_TEMP_MAX`/`HV_BATT_SOC`，就静默用随机池填充。实车联调务必先确认 0x4D8、0x5A2 报文已解码（看 `[CAN-DEBUG] 收到信号数` 与信号内容）。

**Q4：CSV 落盘失败会影响 RMS 吗？**
不会。画像直接吃内存 DataFrame（`_build_rms_df`），CSV 仅调试用途，`_export_rms_window_csv` 异常会被捕获并打印，不影响主流程。

**Q5：为什么落盘 CSV 里 soc/accel_pedal 是整数？**
`_format_rms_csv_df` 对两列做 `_round_half_up` 四舍五入（与模型样例 `test01.csv` 对齐）；其余列保留原始浮点。

**Q6：画像多久更新一次？**
每切一段（约 310s）提交一次后台计算，下一帧 `output_data` 反映新画像。`step()` 不阻塞，RMS 线程池 `max_workers=1` 串行。

---

## 7. 附：模型完整输出字段参考（扩展用）

`rms_call.analyze_df` 仅取子集；如需扩展，模型完整输出含四个部分（详见 `RMS/接口说明书.md`）：

- `window_summary`（13 项实测）：`frame_count, window_seconds, avg_speed_kmh, max_speed_kmh, speed_std_dev, idle_ratio_pct, high_speed_ratio_pct, distance_km, energy_kwh, energy_per_100km, soc_start_pct, soc_end_pct, soc_drop_pct`
- `driving_style_profile`（13 项）：`driving_style, style_confidence, aggressiveness_score, style_percentile, hard_accel_rate, hard_decel_rate, accel_intensity_level, braking_intensity_level, speed_stability_score, pedal_smoothness_score, regen_utilization_level, energy_efficiency_rank, eco_driving_score`
- `driving_scenario_profile`（13 项）：`driving_scenario, scenario_confidence, congestion_level, road_type_estimate, stop_go_frequency, idle_ratio_pct, high_speed_ratio_pct, cruise_ratio_pct, scenario_energy_baseline, scenario_range_baseline_km, scenario_stability, window_avg_speed_kmh, window_distance_km`
- `meta`：`status, data_quality_score, model_version, generated_at, warnings, abnormal_code, abnormal_reason, abnormal_detail`
