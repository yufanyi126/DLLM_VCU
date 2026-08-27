# CANFDNET 系列 CAN 报文收发软件 — 开发手册

## 1. 项目概述

### 1.1 功能
基于周立功（ZLG）CANFDNET 系列以太网 CAN 卡，实现 CAN/CANFD 报文的**收发**，以及 V2_BSI_512 报文的**事件驱动转发**（CAN2 收到 0x512 → 触发 CAN0 发送，最小间隔 10ms）。

### 1.2 前置条件

1. PC 网口与 CANFDNET 设备需在同一网段（默认 `192.168.1.x`）
2. **首次使用前**，请先在 ZLG 官方上位机（ZCANPRO 2.2.5）上跑通连接，确认网络通讯正常，注意端口设置为1030，更多配置见说明及配置上位机 http://39.108.220.80/download/user/ZQWL/CANFDNET/
3. 软件下载：http://39.108.220.80/download/user/ZQWL/CANFDNET/ZCANPRO_Setup_V2.2.5(20230203).rar
4. CANFDNET 系列使用方法：https://manual.zlg.cn/web/#/151/5352

### 1.3 文件清单

| 文件 | 用途 |
|------|------|
| `CANFDNET系列.py` | 主程序：连接、收发线程、V2_BSI_512 发送、看门狗诊断 |
| `zlgcan.py` | ZLG CANFDNET 底层 ctypes 封装 |
| `StlaAIVCU.dbc` | CAN 报文定义数据库 |
| `kerneldlls/` | ZLG 底层 DLL（CANFDNET.dll 等） |

### 1.4 依赖

```python
import os
from zlgcan import *        # ZLG CANFDNET SDK
import threading             # 多线程
import time
import cantools              # DBC 解析
```

### 1.5 支持的设备类型
CANFDNET-100/200U/400U/800U（TCP/UDP），本项目使用4CAN口，选用CANFDDTU-400 系列。设备类型枚举见 `zlgcan.py` L12-L99。

### 1.6 连接模式

当前仅保留一种连接模式：

| 函数 | 模式 | 说明 |
|------|------|------|
| `start_client_only(n)` | PC 作为 TCP Client | PC 连接 CANFDNET 服务器 |

`__main__` 使用 `start_client_only(4)`：PC 作为 TCP Client，连接 `192.168.1.253:1030`，绑定 4 个通道。

> 注：ZLG SDK 还支持 `start_server_only`、`start_server_all`、`start_client_all`、`start_UDP_only`、`start_UDP_all` 等模式，如需使用可参考 ZLG 官方示例恢复。

### 1.7 可配置标志位

程序顶部定义了以下全局标志位，用于控制功能开关：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `work_port` | `["1030"]` | TCP 连接目标端口（CANFDNET 设备端口） |
| `local_port` | `["1030"]` | TCP 本地端口（PC 端，Client 模式下一般不需修改） |
| `V2_BSI_512_MIN_INTERVAL_MS` | `10` | 0x512 最小发送间隔（ms） |
| `CAN0_512_WATCHDOG_MAX` | `50` | 看门狗报警阈值（帧数） |

---

## 2. SDK 底层结构（zlgcan.py）

### 2.1 ZCAN_CAN_FRAME — CAN 帧结构体

```python
# zlgcan.py L294-L303
class ZCAN_CAN_FRAME(Structure):
    _fields_ = [
        ("can_id", c_uint, 32),    # CAN ID，bit31=扩展帧，bit30=远程帧
        ("can_dlc", c_ubyte),      # 数据长度
        ("_pad",  c_ubyte),        # bit5=发送回显 bit6=队列精度 bit7=队列发送
        ("_res0", c_ubyte),        # 队列发送间隔高字节
        ("_res1", c_ubyte),        # 队列发送间隔低字节
        ("data",  c_ubyte * 8),    # 8 字节数据
    ]
```

### 2.2 `_pad` 字段详解

| bit | 值 | 含义 |
|-----|-----|------|
| bit5 | `0x20` | **发送回显**：设置后 `ZCAN_Transmit` 会同步等待硬件回显报文返回才返回 |
| bit6 | `0x40` | 队列发送时间精度（1ms → 0.1ms） |
| bit7 | `0x80` | 队列发送模式 |

**`_pad |= 0x20` 的风险**：如果设备断开、总线故障，回显报文永远不会来，`Transmit` 永久阻塞，调用线程卡死。

### 2.3 关键 API

```python
# 设备操作
zcanlib.OpenDevice(device_type, device_index, reserved)  # 打开设备
zcanlib.ZCAN_SetValue(handle, path, value)               # 设置参数
zcanlib.InitCAN(device_handle, chn, config)              # 初始化通道
zcanlib.StartCAN(chn_handle)                             # 启动通道

# 发送
zcanlib.Transmit(chn_handle, msgs, count)                # 发送 CAN

# 接收
zcanlib.GetReceiveNum(chn_handle, type)                  # 查询可读报文数
zcanlib.Receive(chn_handle, num, timeout_ms)             # 接收 CAN
zcanlib.ReceiveFD(chn_handle, num, timeout_ms)           # 接收 CANFD

# 合并收发
zcanlib.TransmitData(device_handle, msg, len)            # 合并发送
zcanlib.ReceiveData(device_handle, num, timeout_ms)      # 合并接收
```

通道级 `set_device_tx_echo` 配置：
```python
# 在 InitCAN 前对所有通道设置 — 回显由接收线程异步收到
zcanlib.ZCAN_SetValue(device_handle,
    str(i) + "/set_device_tx_echo", "1".encode("utf-8"))
```

### 2.4 DBC 解析（cantools）

使用 `cantools` 库解析 `StlaAIVCU.dbc` 文件，实现 CAN 报文的编解码。

#### 加载 DBC 文件

```python
import cantools

dbc_path = os.path.join(os.path.dirname(__file__), "StlaAIVCU.dbc")

def load_dbc(path):
    """使用 cantools 库加载 DBC 文件"""
    return cantools.database.load_file(path)
```

在 `main` 中调用一次，返回的 `dbc_messages` 对象传递给所有线程：
```python
dbc_messages = load_dbc(dbc_path)
```

#### 解码 CAN 报文（接收线程使用）

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

- 输入：`db`（dbc 对象）、`frame_id`（CAN ID）、`data_bytes`（原始数据）
- 输出：`(msg_def, {信号名: (值, 单位)})` 或 `(None, None)`（ID 不在 DBC 中）
- 用于接收线程打印解码后的信号值，以及发送线程获取 `msg_def` 用于 `encode`

#### 编码 CAN 报文（发送线程使用）

```python
msg_def = dbc_messages.get_message_by_frame_id(0x512)  # 获取报文定义
encoded = msg_def.encode({
    'TBD': 0,
    'STDE_DRV_ENGY_MODE_REQ': engy_val,
    'STDE_DRV_DYN_MODE_REQ': dyn_val,
})
# encoded 是 8 字节 bytearray，按 DBC 定义的 bit 布局编码
```

- `msg_def.encode(dict)` → 返回 `bytes`，自动按 DBC 信号定义布局 bit 位
- 发送线程用位掩码只覆盖目标信号所在 bit，保留其他 bit 的原始数据

---

## 3. 软件架构

### 3.1 线程模型

```
当前配置: start_client_only(4) → 1设备句柄 4通道 → 每通道独立线程

main (阻塞在 input())
 ├── receive_thread(handles[0], chn_handles[0], chn=0)  ← CAN0 接收
 │    └── CAN0 0x512 → 清零看门狗计数器 (g_can0_512_watchdog = 0)
 ├── receive_thread(handles[0], chn_handles[1], chn=1)  ← CAN1 接收
 ├── receive_thread(handles[0], chn_handles[2], chn=2)  ← CAN2 接收
 │    └── CAN2 0x512 → 写入 g_last_512_data + 触发 g_can2_512_event
 ├── receive_thread(handles[0], chn_handles[3], chn=3)  ← CAN3 接收
 │
 ├── send_v2_bsi_512_thread    # V2_BSI_512 事件驱动发送（CAN0）
 │    ├── 等待 g_can2_512_event（CAN2 收到 0x512 才触发，无数据不发送）
 │    ├── 10ms 最小发送间隔限频
 │    ├── 读取 g_last_512_data 作为数据基准
 │    ├── 覆盖两个 DBC 信号位
 │    ├── zcanlib.Transmit(chn_handle, can_msgs, 1)
 │    └── 看门狗计数器 +1 (g_can0_512_watchdog += 1)
 │
 └── diag_can0_512_thread      # 看门狗诊断（5Hz，独立运行）
      └── 读取看门狗计数器，超过阈值则报警
```

### 3.2 全局变量一览

| 变量 | 作用 | 写入者 | 读取者 |
|------|------|--------|--------|
| `thread_flag` | 主循环控制 | main | 所有线程 |
| `print_lock` | 打印互斥 | 接收线程 | 接收线程 |
| `V2_BSI_512_MIN_INTERVAL_MS` | 最小发送间隔（默认 10ms） | - | 发送线程 |
| `g_can2_512_event` | CAN2 收到 0x512 时置位，触发发送线程 | 接收线程(CAN2) | 发送线程 |
| `g_last_512_data` + lock | CAN2 最后一帧 0x512 原始数据 | 接收线程(CAN2) | 发送线程 |
| `g_can0_512_watchdog` + lock | 看门狗计数器（发送++，回显清零） | 发送/接收线程 | 诊断线程 |
| `CAN0_512_WATCHDOG_MAX` | 看门狗阈值（50帧） | - | 诊断线程 |
| `g_use_rolling_values` | True=滚动值 0~F，False=外部控制 | 外部 | 发送线程 |
| `g_engy_mode_req` | 外部接口：energy mode 值 | 外部 | 发送线程 |
| `g_dyn_mode_req` | 外部接口：dynamic mode 值 | 外部 | 发送线程 |
| `CAN_MSG_CHANNEL_MAP` | CAN ID → 允许接收的通道列表 | - | 接收线程 |
| `g_latest_decoded_signals` + lock | 所有 CAN ID 的最新解码信号物理值 | 接收线程 | `get_all_signals()` |

### 3.3 接收线程逻辑（`receive_thread`）

每个通道一个接收线程，主循环 5ms 轮询一次，依次处理三种类型的接收：

```
每轮循环 (5ms):
 ├── 1. CAN 标准帧接收 (GetReceiveNum + Receive)
 │    ├── CAN2 收到 0x512 → 保存原始数据 + 触发事件
 │    ├── CAN0 收到 0x512 → 清零看门狗
 │    └── DBC 解码 → 通道校验 → 存储信号值 + 打印
 ├── 2. CANFD 帧接收 (GetReceiveNum + ReceiveFD)
 │    └── DBC 解码 → 通道校验 → 存储信号值 + 打印
 └── 3. 合并接收 (GetReceiveNum + ReceiveData)
      └── DBC 解码 → 通道校验 → 存储信号值 + 打印
```

**通道校验逻辑**：DBC 解码后，检查 `CAN_MSG_CHANNEL_MAP` 配置表：
- 该 CAN ID 配置了允许通道 → 当前通道匹配才存储信号值 **并** 打印
- 该 CAN ID 配置了允许通道 → 当前通道不匹配 → **不存储、不打印**
- 该 CAN ID 未在配置表中 → **不存储、不打印**

**CAN 报文通道绑定表**：

| CAN ID | 报文名 | 允许通道 | 说明 |
|--------|--------|---------|------|
| 0x278 | CMM_278 | CAN1 | 踏板加速 |
| 0x228 | CMM_228 | CAN1 | 踏板加速 |
| 0x512 | V2_BSI_512 | CAN2 | 信号请求 |
| 0x5E2 | VCU_5E2 | CAN2 | 模式状态反馈 |
| 0x5A2 | VCU_5A2 | CAN2 | 电池 SOC |
| 0x4FE | ESM_4FE | CAN2 | 换挡杆位置 |
| 0x38D | ABR_38D | CAN2 | 车轮速度 |
| 0x522 | E_VCU_522 | CAN2 | 电池温度 |

**接收输出格式**（每次打印所有已积累的信号值，与 `get_all_signals` 一致）：
```
[时间戳] CAN2 ID: 0x5e2 VCU_5E2 DLC:8 TBD=0 STDE_DRV_ENGY_MODE_REQ=3 STDE_DRV_DYN_MODE_REQ=5 STDE_DRV_ENGY_MODE_STATE=2 STDE_DRV_DYN_MODE_STATE=1 HV_BATT_SOC=85.3 ...
```

**接收缓冲策略**：每次最多取 100 帧，避免单次处理过多导致延迟。

### 3.4 统一读取接口（`get_all_signals`）

外部调用此函数获取所有 DBC 信号物理值和看门狗状态：

```python
info = get_all_signals()
# info = {
#     "EFCMNT_PDLE_ACCEL_228": 25.0,
#     "EFCMNT_PDLE_ACCEL_278": 25.0,
#     "STDE_DRV_ENGY_MODE_STATE": 2,
#     "STDE_DRV_DYN_MODE_STATE": 4,
#     "HV_BATT_SOC": 85.3,
#     "TBD": 0,
#     "STDE_DRV_ENGY_MODE_REQ": 3,
#     "STDE_DRV_DYN_MODE_REQ": 5,
#     "POS_MONOSTABLE_LEVER": 1,
#     "VITESSE_VEHICULE_ROUES": 60.25,
#     "HV_BATT_TEMP_AVG": 35,
#     "_watchdog_fault": False,
# }
```

- 信号名直接作为 key，值为物理值（float/int）
- 仅包含已收到且通道匹配的报文信号
- `_watchdog_fault`：`True` = CAN0 发送 0x512 中断

---

## 4. V2_BSI_512 报文发送子系统

### 4.1 业务逻辑

1. **事件驱动**：CAN2 收到 0x512 后通过 `threading.Event` 触发 CAN0 发送；CAN2 无数据时 CAN0 不发送
2. 最小发送间隔 10ms（`V2_BSI_512_MIN_INTERVAL_MS`），防止过快发送
3. CAN ID = 1298（0x512），DLC = 8
4. 数据以 **CAN2 收到的最后一帧 0x512** 原始数据为基准
5. 通过 `cantools.encode` 覆盖 byte0 中的信号位（bit2-bit7），byte1 完整复制 CAN2 原始数据
6. 发送后看门狗计数器 +1

**滚动值模式**（`g_use_rolling_values = True`，默认）：
- 每次发送 `roll_counter` 递增（0~15 循环）
- `STDE_DRV_ENGY_MODE_REQ = roll_counter & 0x7`（0~7 循环）
- `STDE_DRV_DYN_MODE_REQ = (roll_counter >> 1) & 0x7`（0~7 循环）
- 用于测试场景，模拟信号变化

**外部控制模式**：调用 `set_drv_mode_values()` 后自动切换，使用固定值发送。

### 4.2 发送线程代码（`send_v2_bsi_512_thread`）

```python
def send_v2_bsi_512_thread(chn_handle, chn_index, dbc_messages):
    global g_can0_512_watchdog
    v2_bsi_512_id = 1298
    msg_def = dbc_messages.get_message_by_frame_id(v2_bsi_512_id)
    min_interval_sec = V2_BSI_512_MIN_INTERVAL_MS / 1000.0
    last_send_time = 0.0

    can_msgs = (ZCAN_Transmit_Data * 1)()
    can_msgs[0].transmit_type = 0
    can_msgs[0].frame.can_id = 1298
    can_msgs[0].frame.can_dlc = 8

    while thread_flag:
        # 1. 等待 CAN2 收到 0x512 事件，超时则跳过（不发送）
        if not g_can2_512_event.wait(timeout=0.5):
            continue
        g_can2_512_event.clear()

        if not thread_flag:
            break

        # 2. 频率限制：确保两次发送间隔不小于 10ms
        now = time.time()
        elapsed = now - last_send_time
        if elapsed < min_interval_sec:
            time.sleep(min_interval_sec - elapsed)

        # 3. 从 CAN2 拷贝原始数据
        with g_last_512_data_lock:
            for j in range(8):
                can_msgs[0].frame.data[j] = g_last_512_data[j]

        # 4. 选择信号值（滚动或外部控制）
        if g_use_rolling_values:
            engy_val = roll_counter & 0x7
            dyn_val = (roll_counter >> 1) & 0x7
        else:
            engy_val = g_engy_mode_req
            dyn_val = g_dyn_mode_req

        # 5. cantools encode + 位掩码覆盖 byte0 信号位，byte1 完整保留
        encoded = msg_def.encode({'TBD': 0,
            'STDE_DRV_ENGY_MODE_REQ': engy_val,
            'STDE_DRV_DYN_MODE_REQ': dyn_val})
        # byte0: bit2~bit7 用 encode 结果覆盖（信号位），bit0~bit1 保留 CAN2 原始数据
        can_msgs[0].frame.data[0] = (encoded[0] & 0xFC) | (can_msgs[0].frame.data[0] & 0x03)
        # byte1: 完整复制 CAN2 原始数据，不做任何信号覆盖

        # 6. 发送 + 看门狗计数
        zcanlib.Transmit(chn_handle, can_msgs, 1)
        last_send_time = time.time()
        if chn_index == 0:
            with g_can0_512_watchdog_lock:
                g_can0_512_watchdog += 1

        roll_counter = (roll_counter + 1) % 16
```

**关键变化**：`event.wait(timeout=0.5)` 返回 `False`（超时）时 `continue` 跳回等待，CAN2 无数据时 CAN0 不发送。

### 4.3 接收线程中的 0x512 处理

```python
# CAN2 收到 0x512 → 保存原始数据 + 触发发送线程
if chn_index == 2 and can_id == 1298 and dlc > 0:
    with g_last_512_data_lock:
        for j in range(min(dlc, 8)):
            g_last_512_data[j] = frame.data[j]
    g_can2_512_event.set()  # 触发 CAN0 发送

# CAN0 收到 0x512 → 清零看门狗计数器（回显正常）
if chn_index == 0 and can_id == 1298:
    with g_can0_512_watchdog_lock:
        g_can0_512_watchdog = 0
```

### 4.4 外部控制接口

```python
def set_drv_mode_values(engy_mode, dyn_mode):
    """
    Python函数接口：由其他函数控制 STDE_DRV_ENGY_MODE_REQ 和 STDE_DRV_DYN_MODE_REQ 的值。
    调用此函数后将自动切换到外部变量控制模式（关闭滚动值）。
    engy_mode: 0~7, STDE_DRV_ENGY_MODE_REQ 的值
    dyn_mode:  0~7, STDE_DRV_DYN_MODE_REQ 的值
    """
    global g_engy_mode_req, g_dyn_mode_req, g_use_rolling_values
    g_engy_mode_req = engy_mode & 0x7
    g_dyn_mode_req = dyn_mode & 0x7
    g_use_rolling_values = False  # 外部设置值时自动切换为非滚动模式
```

- 参数范围：`engy_mode` 和 `dyn_mode` 均为 0~7（3bit），高位自动截断
- 调用后立即生效，发送线程下一次发送即使用新值
- **不可逆**：调用后无法通过此函数恢复滚动模式，需重启程序

### 4.5 滤波配置（`Set_Filter`，可选）

> 仅 CANFDNET-400U 支持，当前默认注释关闭。

```python
def Set_Filter(device_handle, chn):
```

设置白名单滤波，仅接收指定 ID 范围的报文。流程：

1. `filter_clear` → 清除已有滤波
2. 设置滤波组（模式 + 起始ID + 结束ID）：
   - 第一组：标准帧 0x000~0x07F
   - 第二组：标准帧 0x0FF~0x1FF
   - 第三组：扩展帧 0x0FF~0x2FF
3. `filter_ack` → 使能滤波

设备支持最多 16 组滤波。如需启用，在 `__main` 中取消注释：
```python
# Set_Filter(handles[0], 0)  # 对通道0设置滤波
```

---

## 5. 看门狗诊断机制

### 5.1 设计原理

采用经典**生产者-消费者看门狗**模式：

```
发送线程 ──(+1)──→ g_can0_512_watchdog ──(检查)──→ 诊断线程
                     ↑
接收线程 ──(清零)───┘
```

- **发送线程**：每调用一次 `Transmit`，计数器 +1
- **接收线程**：每次收到 CAN0 的 0x512 回显报文，计数器清零
- **诊断线程**（5Hz 独立运行）：读取计数器，超过 `CAN0_512_WATCHDOG_MAX`（50帧）则报警
- **事件驱动联动**：CAN2 停止发送 0x512 → CAN0 停止发送 → 看门狗计数器不再增长 → 诊断自然停止，不会误报

三者之间仅通过一个整数计数器通信，发送线程和诊断线程完全解耦。即便 `Transmit` 永久阻塞导致发送线程卡死，计数器停止增长，诊断线程依然能正常检测到回显中断。

### 5.2 诊断线程代码（`diag_can0_512_thread`）

```python
def diag_can0_512_thread():
    DIAG_PERIOD = 0.2  # 5Hz
    warned = False

    while thread_flag:
        time.sleep(DIAG_PERIOD)
        with g_can0_512_watchdog_lock:
            cnt = g_can0_512_watchdog

        if cnt > CAN0_512_WATCHDOG_MAX:
            if not warned:
                print(f"[DIAG] CAN0 0x512 发送中断！看门狗计数={cnt}", flush=True)
                warned = True
        else:
            if warned:
                print(f"[DIAG] CAN0 0x512 发送已恢复（看门狗计数={cnt}）", flush=True)
                warned = False
```

### 5.3 诊断输出示例

```
[DIAG] CAN0 0x512 发送中断！看门狗计数=51 (>阈值50)
[DIAG] CAN0 0x512 发送已恢复（看门狗计数=0）
```

- 仅状态翻转时打印（中断出现一次、恢复出现一次），不刷屏
- `flush=True` 确保立即输出
- 不使用 `print_lock`，不与接收线程争锁

---

## 6. 问题历史与演进

### 6.1 V1：发送线程内置诊断 → 失败
- 诊断代码（ret 检查、回显检查）写在 `Transmit` 之后
- `Transmit` 阻塞时后续代码永远执行不到 → 无任何错误输出

### 6.2 V2：独立诊断线程（时间戳方案）→ 部分有效
- 发送线程在 Transmit 前后记录时间戳到共享变量
- 诊断线程通过比较时间戳判断阻塞
- 问题：时间戳比较逻辑复杂，且诊断 `print` 共用 `print_lock` 被接收线程阻塞

### 6.3 V3：超时线程方案 → 过度设计
- Transmit 在 daemon 线程中执行，`join(timeout=0.5s)`
- 超时后主循环继续
- 问题：逻辑复杂，线程堆积风险，Transmit 阻塞的 daemon 线程无法回收

### 6.4 V4：看门狗计数器方案
- 发送线程只做 `++`，接收线程只做清零，诊断线程只读计数器
- 优点：极简、无锁竞争、不依赖时间戳、Transmit 阻塞不影响诊断

### 6.5 V5：事件驱动发送
- CAN2 收到 0x512 时通过 `threading.Event` 触发 CAN0 发送
- `event.wait(timeout=0.5)` 超时时 `continue` 跳过，CAN2 无数据时 CAN0 不发送
- 10ms 最小发送间隔限频（`V2_BSI_512_MIN_INTERVAL_MS`）
- 看门狗自然联动：CAN2 停 → CAN0 停 → 计数器不增长 → 诊断不误报

### 6.6 V6：通道绑定 + 统一读取接口 + 全信号打印（当前版本）
- 新增 `CAN_MSG_CHANNEL_MAP`：每条 CAN 报文绑定到指定通道，非允许通道收到时不存储、不打印
- 新增 `g_latest_decoded_signals`：持久化存储所有已接收信号的最新物理值
- 新增 `get_all_signals()`：统一外部读取接口，返回所有信号物理值 + 看门狗故障状态
- 接收线程打印格式改为输出所有已积累信号值（与 `get_all_signals` 一致）

### 6.7 V7：移除合并接收标志位（当前版本）
- 移除 `enable_merge_receive` 全局变量及 `ZCAN_SetValue` 中 `set_device_recv_merge` 调用
- 合并接收（`ReceiveData`）模式在实际设备上无法正常运行，彻底去除该分支
- 线程创建简化为：每通道一个独立接收线程，不再有合并/独立的条件判断
- 退出时直接遍历 join 所有接收线程，无需区分模式

---

## 7. 启动流程

```
main
 ├── zcanlib = ZCAN()
 ├── dbc_messages = load_dbc(dbc_path)
 ├── handles, chn_handles = start_client_only(4)
 │    ├── OpenDevice(ZCAN_CANFDNET_400U_TCP)
 │    ├── SetValue: work_mode=0, work_port, ip="192.168.1.253"
 │    ├── for i in 0..3:
 │    │    ├── SetValue: set_device_tx_echo=1（通道级回显）
 │    │    ├── InitCAN → StartCAN
 │    │    └── chn_handles.append(chn_handle)
 │    └──├── Read_Device_Info
 ├── 启动接收线程（每通道一个独立线程，共 4 个）
 ├── 启动 send_v2_bsi_512_thread(chn_handles[0], 0)  # 事件驱动，等待 CAN2 触发
 ├── 启动 diag_can0_512_thread()（daemon 线程）
 └── input() 等待退出
      └── thread_flag = False → 各线程退出 → 关闭通道/设备
```

### 7.2 退出流程

```
用户按回车
 ├── thread_flag = False（通知所有线程退出循环）
 ├── 逐个 join 所有通道接收线程
 ├── for each 通道: ResetCAN(chn_handle) → 关闭通道
 └── for each 设备: CloseDevice(device_handle) → 关闭设备
```

> `diag_can0_512_thread` 是 daemon 线程，随主线程退出自动终止，无需 join。

---

## 8. 附录：常用操作

### 8.1 修改 0x512 最小发送间隔

```python
V2_BSI_512_MIN_INTERVAL_MS = 10   # 改为 20 即最大 50Hz
```

### 8.2 修改看门狗超时阈值

```python
CAN0_512_WATCHDOG_MAX = 50   # 50帧未收到回显则报警
```

### 8.3 切换到外部信号值控制

```python
set_drv_mode_values(engy_mode=3, dyn_mode=5)
```

### 8.4 修改报文通道绑定

修改 `CAN_MSG_CHANNEL_MAP` 字典，key 为 CAN ID（十六进制），value 为允许接收的通道索引列表：
```python
CAN_MSG_CHANNEL_MAP = {
    0x278: [1],           # CMM_278: 只从 CAN1
    0x512: [2],           # V2_BSI_512: 只从 CAN2
    # 新增报文：
    0x123: [0, 2],        # 某报文: CAN0 和 CAN2 都接收
}
```

### 8.5 读取所有 DBC 信号值

```python
info = get_all_signals()
print(info["HV_BATT_SOC"])          # → 85.3
print(info["_watchdog_fault"])       # → False
```

### 8.6 修改连接方式

当前仅支持 `start_client_only`，如需其他连接模式需恢复对应函数。修改通道数：
```python
handles, chn_handles = start_client_only(4)  # 改为实际通道数
```

### 8.7 修改目标 IP

在 `start_client_only` 中修改：
```python
zcanlib.ZCAN_SetValue(device_handle, "0/ip", "192.168.1.253".encode("utf-8"))
```
