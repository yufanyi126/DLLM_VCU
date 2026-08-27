# CANFDNET.py — 车辆数据读写说明

> 文件位置：`CANLan/CANFDNET.py`  
> 依赖 DBC 文件：`CANLan/StlaAIVCU.dbc`  
> 本文件是对 CANFDNET 硬件的封装，负责读取车辆 VCU 上报的参数、以及向车辆下发控制指令。

---

## 一、总体数据流

```
┌────────── 读：车辆上报数据到 Python ──────────┐
│                                               │
│  VCU  ──CAN总线──► CANFDNET硬件                │
│                      │                        │
│    ┌─────────────────┘                        │
│    ▼                                          │
│  receive_thread()    第168行  200Hz轮询        │
│    │                                          │
│    ▼                                          │
│  decode_dbc_signals() 第86行  DBC解码          │
│    │                                          │
│    ▼                                          │
│  g_latest_decoded_signals 第77行 全局缓存       │
│    │                                          │
│    ▼                                          │
│  get_all_signals()    第357行 外部读接口        │
│    │                                          │
│    ▼                                          │
│  main.py → step() → demo.py                   │
└───────────────────────────────────────────────┘

┌────────── 写：Python 下发指令到车辆 ───────────┐
│                                               │
│  demo.py → set_drv_mode_values() 第343行       │
│    │ 设置全局变量                              │
│    ▼                                          │
│  g_engy_mode_req / g_dyn_mode_req 第32-33行   │
│    │                                          │
│    ▼                                          │
│  send_v2_bsi_512_thread() 第459行 10Hz定时     │
│    │ DBC编码 → CAN0发送                       │
│    ▼                                          │
│  CANFDNET硬件 ──CAN总线──► VCU                 │
└───────────────────────────────────────────────┘
```

---

## 二、读取车辆参数 — 共 5 个关键位置

### 位置 1：通道映射表 — 第 64~74 行（改 CAN ID 的收发通道）

```python
CAN_MSG_CHANNEL_MAP = {
    0x278: [1],           # CMM_278:      只从 CAN1
    0x228: [1, 2],        # CMM_228:      实车 CAN1，测试时借道 CAN2（CAN1 物理总线无 ACK）
    0x512: [2],           # V2_BSI_512:   只从 CAN2
    0x5E2: [2],           # VCU_5E2:      只从 CAN2
    0x5A2: [2],           # VCU_5A2:      只从 CAN2
    0x4FE: [2],           # ESM_4FE:      只从 CAN2
    0x38D: [2],           # ABR_38D:      只从 CAN2
    0x522: [2],           # E_VCU_522:    只从 CAN2
    0x03C: [3],           # 03C:    只从 CAN3
}
```

**作用**：定义哪些 CAN ID 从哪个硬件通道接收并解码。未配置的 CAN ID **不做 DBC 解析**，也不存入信号缓存。

**各 CAN ID 含义**：

| CAN ID | 报文名称 | 来源通道 | 包含的信号 |
|--------|---------|---------|-----------|
| `0x278` | CMM_278 | CAN1 | 油门踏板开度 `EFCMNT_PDLE_ACCEL_278` |
| `0x228` | CMM_228 | CAN1/CAN2 | 油门踏板开度 `EFCMNT_PDLE_ACCEL_228`（测试时走 CAN2） |
| `0x512` | V2_BSI_512 | CAN2 | 能量/驾驶模式**请求**值 `STDE_DRV_ENGY_MODE_REQ`、`STDE_DRV_DYN_MODE_REQ` |
| `0x5E2` | VCU_5E2 | CAN2 | 能量模式**状态** `STDE_DRV_ENGY_MODE_STATE`、驾驶模式**状态** `STDE_DRV_DYN_MODE_STATE` |
| `0x5A2` | VCU_5A2 | CAN2 | 高压电池 SOC `HV_BATT_SOC` |
| `0x4FE` | ESM_4FE | CAN2 | 档位 `POS_MONOSTABLE_LEVER` (0=P, 1=R, 2=N, 3=D) |
| `0x38D` | ABR_38D | CAN2 | 轮速 `VITESSE_VEHICULE_ROUES` |
| `0x522` | E_VCU_522 | CAN2 | 电池平均温度 `HV_BATT_TEMP_AVG` |
| `0x03C` | 03C | CAN3 | （待补充） |

**如何修改**：若要新增/删除监听的 CAN ID，直接增删字典条目即可。格式为 `CAN_ID: [通道索引列表]`。

---

### 位置 2：DBC 解码函数 — 第 86~97 行（通常不需要改）

```python
def decode_dbc_signals(db, frame_id, data_bytes):
    """使用 cantools 解码 CAN 报文数据"""
    try:
        msg = db.get_message_by_frame_id(frame_id)
        decoded = msg.decode(data_bytes)
        result = {}
        for sig in msg.signals:
            if sig.name in decoded:
                result[sig.name] = (decoded[sig.name], sig.unit or "")
        return msg, result
    except (KeyError, Exception):
        return None, None
```

**作用**：通过 `cantools` 库加载的 DBC 文件，把原始 8 字节 CAN 数据自动解码为有意义的物理值（如 车速=50.0 km/h、SOC=78.5%）。

> **不需要改**：如果 DBC 文件更新了，直接替换 `CANLan/StlaAIVCU.dbc` 即可，这个函数会自动适配。

---

### 位置 3：接收线程 — 第 168~223 行（通常不需要改）

```python
def receive_thread(device_handle, chn_handle, chn_index, dbc_messages):
    global g_can0_512_watchdog
    while thread_flag:
        time.sleep(0.005)                         # 200Hz 轮询
        rcv_num = zcanlib.GetReceiveNum(chn_handle, ZCAN_TYPE_CAN)
        if rcv_num:
            for msg in rcv_msg[:rcv_num]:
                # ... 提取 can_id 和 data_bytes ...
                
                # CAN2 收到 0x512 → 保存原始数据为发送模板
                if chn_index == 2 and can_id == 1298 and dlc > 0:
                    with g_last_512_data_lock:
                        for j in range(min(dlc, 8)):
                            g_last_512_data[j] = frame.data[j]

                # CAN0 收到 0x512 → 清零看门狗（回显正常）
                if chn_index == 0 and can_id == 1298:
                    with g_can0_512_watchdog_lock:
                        g_can0_512_watchdog = 0

                # DBC 解码 + 通道校验
                dbc_msg, decoded = decode_dbc_signals(dbc_messages, can_id, data_bytes)
                if dbc_msg is not None:
                    allowed = CAN_MSG_CHANNEL_MAP.get(can_id)
                    if allowed is None or chn_index in allowed:
                        with g_latest_decoded_signals_lock:
                            g_latest_decoded_signals[can_id] = {k: v for k, (v, _) in decoded.items()}
```

**作用**：后台线程，200Hz 持续从 CANFDNET 硬件拉取 CAN 报文 → 通道校验（只保留 `CAN_MSG_CHANNEL_MAP` 中配置的 ID）→ DBC 解码 → 存入 `g_latest_decoded_signals` 全局缓存。

**两个特殊处理**：
- **CAN2 收到 0x512**（第 199 行）：保存原始 8 字节数据，作为后续发送 0x512 控制报文时的模板（保留非信号数据位）
- **CAN0 收到 0x512**（第 206 行）：这是发送回显（TX Echo），说明 0x512 发送正常，清零看门狗计数器

---

### 位置 4：全局信号缓存 — 第 76~78 行（通常不需要改）

```python
# 所有 CAN ID 的最新解码信号物理值（接收线程写入，get_all_signals 读取）
g_latest_decoded_signals = {}   # dict[int, dict[str, float]]
g_latest_decoded_signals_lock = threading.Lock()
```

**数据结构示例**：
```python
{
    0x5E2: {"STDE_DRV_ENGY_MODE_STATE": 0.0, "STDE_DRV_DYN_MODE_STATE": 1.0},
    0x5A2: {"HV_BATT_SOC": 78.5},
    0x4FE: {"POS_MONOSTABLE_LEVER": 3.0},
    0x38D: {"VITESSE_VEHICULE_ROUES": 50.0},
    ...
}
```

---

### 位置 5：外部读取接口 — 第 357~382 行（不需要改）

```python
def get_all_signals():
    """统一外部读取接口：返回所有 DBC 信号物理值 + 看门狗故障状态。"""
    result = {}
    with g_latest_decoded_signals_lock:
        for decoded in g_latest_decoded_signals.values():
            for sig_name, value in decoded.items():
                result[sig_name] = value
    with g_can0_512_watchdog_lock:
        result["_watchdog_fault"] = g_can0_512_watchdog > CAN0_512_WATCHDOG_MAX
    return result
```

**作用**：把全局缓存拍平成 `{信号名: 物理值}` 的扁平字典。`main.py` 的 `step()` 每次循环调用它。

**返回值示例**：
```python
{
    "VITESSE_VEHICULE_ROUES": 50.0,           # 车速 (km/h)
    "EFCMNT_PDLE_ACCEL_228": 25.0,            # 油门开度 (%)
    "STDE_DRV_ENGY_MODE_STATE": 0,            # 能量模式状态 (0=EV, 1=HEV)
    "STDE_DRV_DYN_MODE_STATE": 1,             # 驾驶模式状态 (0=ECO, 1=标准, 2=SPORT)
    "HV_BATT_SOC": 78.5,                      # 高压电池 SOC (%)
    "POS_MONOSTABLE_LEVER": 3,                # 档位 (0=P, 1=R, 2=N, 3=D)
    "HV_BATT_TEMP_AVG": 35.0,                 # 电池平均温度 (°C)
    "_watchdog_fault": False,                 # 0x512 发送是否中断
}
```

---

## 三、向车辆下发控制指令 — 共 3 个关键位置

### 位置 1：写入接口 — 第 343~355 行（改动策略时改这里）

```python
def set_drv_mode_values(engy_mode, dyn_mode):
    """
    Python函数接口：由外部函数控制 STDE_DRV_ENGY_MODE_REQ 和 STDE_DRV_DYN_MODE_REQ 的值。
    调用此函数后将自动切换到外部变量控制模式（关闭滚动值）。
    engy_mode: 0~7, STDE_DRV_ENGY_MODE_REQ 的值
    dyn_mode:  0~7, STDE_DRV_DYN_MODE_REQ 的值
    """
    global g_engy_mode_req, g_dyn_mode_req, g_use_rolling_values
    g_engy_mode_req = engy_mode & 0x7
    g_dyn_mode_req = dyn_mode & 0x7
    g_use_rolling_values = False  # 外部设置值时自动切换为非滚动模式
    print(f"[CANFDNET] 0x512 请求更新: ENGY_MODE_REQ={engy_mode}, DYN_MODE_REQ={dyn_mode}")
```

**参数说明**：

| 参数 | 取值范围 | 含义 |
|------|---------|------|
| `engy_mode` | `0` = EV 纯电, `1` = HEV 混动 | 能量模式请求 |
| `dyn_mode` | `0` = ECO 经济, `1` = 标准, `2` = SPORT 运动 | 驾驶模式请求 |

> **注意**：这里设置的只是**请求值**（`_REQ`），最终车辆是否生效要看 VCU 上报的**状态值**（`_STATE`），后者在 0x5E2 报文中。

**调用者** — `demo.py` 的 AI 决策：

```python
# AI 决策 → 反向控制车辆模式
if soc < 30:
    set_drv_mode_values(engy_mode=0, dyn_mode=0)    # 低电量 → EV+ECO
elif speed > 80:
    set_drv_mode_values(engy_mode=1, dyn_mode=2)    # 高速 → HEV+SPORT
elif speed < 30:
    set_drv_mode_values(engy_mode=0, dyn_mode=1)    # 低速 → EV+标准
```

### 位置 2：全局控制变量 — 第 27~36 行（不需要改）

```python
# ==================== V2_BSI_512 报文发送相关 ====================
# 最小发送间隔 (ms)，事件驱动模式下防止过快发送
V2_BSI_512_MIN_INTERVAL_MS = 100

# 信号值变量（由外部函数接口 set_drv_mode_values() 控制）
g_engy_mode_req = 0              # 能量模式请求: 0=EV, 1=HEV
g_dyn_mode_req = 0               # 驾驶模式请求: 0=ECO, 1=标准, 2=SPORT
g_use_rolling_values = False     # False=使用外部变量, True=使用滚动值0~F测试
```

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `V2_BSI_512_MIN_INTERVAL_MS` | `100` | 0x512 报文最小发送间隔，单位毫秒 |
| `g_engy_mode_req` | `0` | 当前要发送的能量模式值 |
| `g_dyn_mode_req` | `0` | 当前要发送的驾驶模式值 |
| `g_use_rolling_values` | `False` | 测试用：`True` 时自动滚动发送 0~F 值，忽略外部设置 |

---

### 位置 3：后台发送线程 — 第 459~522 行（通常不需要改）

```python
def send_v2_bsi_512_thread(chn_handle, chn_index, dbc_messages):
    """
    定时发送 V2_BSI_512 (ID: 0x512=1298) 报文线程。
    每隔 100ms 自动发送一帧。
    以 CAN2 收到的原始 0x512 数据为基准，通过 cantools 解码后仅覆盖两个信号，
    再重新编码发送，其他信号/数据位完整保留。
    """
    # ... 每 100ms 循环 ...
    while thread_flag:
        time.sleep(send_interval_sec)          # 100ms

        # 读取全局变量的值
        if g_use_rolling_values:
            engy_val = roll_counter & 0x7
            dyn_val = (roll_counter >> 1) & 0x7
        else:
            engy_val = g_engy_mode_req        # ← 外部设置的值
            dyn_val = g_dyn_mode_req

        # DBC 编码：把物理值编码回 8 字节 CAN 数据
        encoded = msg_def.encode({
            'TBD': 0,
            'STDE_DRV_ENGY_MODE_REQ': engy_val,
            'STDE_DRV_DYN_MODE_REQ': dyn_val,
        })
        # 只覆盖 byte0 的信号位，其余保留 CAN2 原始数据
        can_msgs[0].frame.data[0] = (encoded[0] & 0xFC) | (can_msgs[0].frame.data[0] & 0x03)
        
        zcanlib.Transmit(chn_handle, can_msgs, 1)    # 通过 CAN0 通道发出
```

**工作原理**：
1. 每 100ms 自动触发一次发送
2. 读取 `g_engy_mode_req` 和 `g_dyn_mode_req`（由 `set_drv_mode_values()` 写入）
3. 用 DBC 编码生成正确的信号 bit 位
4. 只覆盖 byte0 的 bit2~bit7（能量模式 + 驾驶模式），bit0~bit1 保留 CAN2 原始数据
5. 通过 CAN0 通道将 0x512 报文发送出去

---

## 四、信号枚举值速查（定义在 `main.py` 第 76~95 行）

```python
# 驾驶模式 (STDE_DRV_DYN_MODE_STATE, 0x5E2)
DYN_MODE_MAP = {
    0: "ECO",
    1: "标准",
    2: "SPORT",
    3: "自定义",
}

# 档位 (POS_MONOSTABLE_LEVER, 0x4FE)
GEAR_MAP = {
    0: "P",
    1: "R",
    2: "N",
    3: "D",
}

# 能量模式 (STDE_DRV_ENGY_MODE_STATE, 0x5E2)
ENGY_MODE_MAP = {
    0: "EV",
    1: "HEV",
}
```

---

## 五、测试模式配置（无实车时使用）

### 测试报文定义 — 第 636~651 行

```python
# 测试报文配置：(通道索引, CAN ID hex, {信号名: 值, ...})
# 信号值使用 DBC 原始值（物理值），由 cantools.encode() 自动编码为 8 字节数据
_TEST_MSGS_CONFIG = [
    (2, 0x228, {"EFCMNT_PDLE_ACCEL_228": 25.0}),                            # 油门 25%
    (2, 0x5E2, {"STDE_DRV_ENGY_MODE_STATE": 0, "STDE_DRV_DYN_MODE_STATE": 1}), # EV+标准
    (2, 0x5A2, {"HV_BATT_SOC": 78.5}),                                      # SOC 78.5%
    (2, 0x4FE, {"POS_MONOSTABLE_LEVER": 3}),                                # D 档
    (2, 0x522, {"HV_BATT_TEMP_AVG": 35.0}),                                  # 电池 35°C
    (2, 0x38D, {"VITESSE_VEHICULE_ROUES": 50.0}),                            # 车速 50 km/h
]
```

**格式说明**：`(通道索引, CAN_ID, {信号名: 模拟物理值})`

**如何修改模拟值**：直接改第三个元素中的数值即可，例如把 `78.5` 改成 `50.0` 模拟低电量。

### 测试模式控制 — 第 653~729 行

```python
TEST_TX_PERIOD = 0.5          # 发送周期 (秒) = 2Hz

def start_test_tx():          # 第684行 — 启动所有测试报文发送
    """调用前需确保 init_can() 已成功运行。"""
    
def stop_test_tx():           # 第721行 — 停止所有测试报文发送
    """[自发自收测试] 停止测试报文发送。"""
```

**启动/关闭**：通过 `main.py` 中的 `USE_CAN_TEST_TX` 开关控制（第 70 行）。

---

## 六、常见修改场景速查

| 需求 | 修改位置 | 改什么 |
|------|---------|--------|
| 新增监听的 CAN ID | `CANFDNET.py:64` `CAN_MSG_CHANNEL_MAP` | 添加 `CAN_ID: [通道号]` 条目 |
| 修改 DBC 定义 | 替换文件 | 替换 `CANLan/StlaAIVCU.dbc`，代码自动适配 |
| 新增车辆控制类型 | `CANFDNET.py` 新增函数 | 仿照 `set_drv_mode_values()` 写新的设置函数 + 对应的发送线程 |
| 修改 0x512 发送频率 | `CANFDNET.py:29` | 改 `V2_BSI_512_MIN_INTERVAL_MS` |
| 修改测试模拟值 | `CANFDNET.py:638` `_TEST_MSGS_CONFIG` | 改第三个元素的数值 |
| 关闭测试模式（接实车） | `main.py:70` | 设置 `USE_CAN_TEST_TX = False` |
| 修改能量/驾驶模式枚举 | `main.py:76-95` | 改 `DYN_MODE_MAP` / `ENGY_MODE_MAP` / `GEAR_MAP` |
| 修改 AI 决策策略 | `demo.py:43-51` | 改 `if/elif` 条件分支 |

---
