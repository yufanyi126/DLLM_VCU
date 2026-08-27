'''
    支持的设备有 CANFDNET-100 MINI，CANFDNET-200U/400U/800U    CANFDNET-200H,400H  CANFDDTU-400系列及以上
    启动设备的连接本质，其实是建立网络连接，所有请在ZCANPRO/ZXDOC 我们官方上位机软件上跑通再进行例程测试
    Line 17 and 19 对应目标端口与本地端口，这里填写很重要，需要根据网络配置工具的设置进行配置

    软件下载链接：https://manual.zlg.cn/web/#/314/12431
    CANFDNET系列使用方法：https://manual.zlg.cn/web/#/151/5352
'''

from zlgcan import *
import os
import threading
import time
import cantools

thread_flag = True
print_lock = threading.Lock()   # 线程锁，只是为了打印不冲突

# ===== 模块级变量：供外部调用者控制 =====
zcanlib = None              # 由 init_can() 赋值
g_dbc_messages = None       # DBC 数据库对象
g_device_handles = []       # 设备句柄列表
g_chn_handles = []          # 通道句柄列表
g_threads = []              # 所有工作线程


# ==================== V2_BSI_512 报文发送相关 ====================
# 最小发送间隔 (ms)，事件驱动模式下防止过快发送
V2_BSI_512_MIN_INTERVAL_MS = 1000

# 信号值变量（由外部函数接口 set_drv_mode_values() 控制）
g_engy_mode_req = 0
g_dyn_mode_req = 0

# STDE_MODE_REQ_BIT_TGL 翻转信号：任一模式指令（ENGY/DYN）变化时翻转
g_mode_req_bit_tgl = 0
g_mode_req_bit_tgl_lock = threading.Lock()
# 记录上次发送的模式值，用于检测变化
g_last_sent_engy = None
g_last_sent_dyn = None

# 临时屏蔽外部控制，使用滚动值发送：True=滚动值0~F， False=使用外部变量
g_use_rolling_values = False

# CAN2 收到第一帧 0x512 时置位，发送线程等待该事件后才开始定时发送
g_can2_512_first_event = threading.Event()

# CAN1 收到的最后一帧 0x512 原始数据（8字节），作为发送时其他数据位的基准
g_last_512_data = bytearray(8)
g_last_512_data_lock = threading.Lock()

# CAN0 0x512 发送看门狗计数器
# 发送线程每发一帧 ++，接收线程收到 CAN0 0x512 回显时清零
# 诊断线程发现计数器超过阈值则报发送中断
g_can0_512_watchdog = 0
g_can0_512_watchdog_lock = threading.Lock()

# 512 报文发送总开关：True=允许发送，False=禁止发送（即使 AI 决策返回也不发送）
g_512_tx_enabled = True

# 看门狗阈值：50帧未收到回显则报警
CAN0_512_WATCHDOG_MAX = 50

# 目标端口/连接端口 PC作为TCP Server 时不需要关心这个参数
work_port = ["1030"]
# 本地端口 PC作为TCP Client 时不需要关心此参数
local_port = ["1030"]

dbc_path = os.path.join(os.path.dirname(__file__), "StlaAIVCU.dbc")

# ==================== CAN 报文通道绑定 & 信号读取 ====================
# CAN ID → 允许接收并解码的通道索引列表
# 未配置的 CAN ID 不做 DBC 解析，不存入全局信号字典
CAN_MSG_CHANNEL_MAP = {
    0x278: [1],           # CMM_278:      只从 CAN1
    0x228: [1, 2],        # CMM_228:      实车 CAN1，测试时借道 CAN2（CAN1 物理总线无 ACK）
    0x512: [2],           # V2_BSI_512:   只从 CAN2
    0x5E2: [2],           # VCU_5E2:      只从 CAN2
    0x5A2: [2],           # VCU_5A2:      只从 CAN2
    0x4FE: [2],           # ESM_4FE:      只从 CAN2
    0x38D: [2],           # ABR_38D:      只从 CAN2
    0x522: [2],           # E_VCU_522:    只从 CAN2
    0x4D8: [2],           # DAT_CMM_4D8:  电池电流/电压，只从 CAN2
    0x03C: [3],           # 03C:    只从 CAN3
}

# 所有 CAN ID 的最新解码信号物理值（接收线程写入，get_all_signals 读取）
g_latest_decoded_signals = {}   # dict[int, dict[str, float]]
g_latest_decoded_signals_lock = threading.Lock()


def load_dbc(path):
    """使用 cantools 库加载 DBC 文件"""
    return cantools.database.load_file(path)


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

# 读取设备信息
def Read_Device_Info(device_handle):

    # 获取设备信息
    info = zcanlib.GetDeviceInf(device_handle)
    print("设备信息: \n%s" % info)

    can_number = info.can_num
    return can_number

# PC作为TCP客户端 CANFDNET作为TCP服务器 --PC作为一个客户端     can_number传入连接该服务器的绑定的通道数
def start_client_only(can_number=1):

    device_handles = []
    chn_handles = []

    # 打开设备 并不会实际建立连接
    device_handle = zcanlib.OpenDevice(ZCAN_CANFDNET_400U_TCP,0,0)
    if device_handle == INVALID_DEVICE_HANDLE:
        print("打开设备失败！请检查调用库的路径是否正确，以及运行库问题")
        exit(0)
    print(f"设备句柄: {device_handle}.")

    # 没写则不需要设置
    ret = zcanlib.ZCAN_SetValue(device_handle,"0/work_mode", "0".encode("utf-8"))   #0-客户端 1-服务器
    if ret == ZCAN_STATUS_ERR:
        print("设置工作模式失败！")

    ret = zcanlib.ZCAN_SetValue(device_handle, "0/work_port", work_port[0].encode("utf-8"))  # 设置目标端口
    if ret == ZCAN_STATUS_ERR:
        print("设置目标端口失败！")

    # 配置目标IP，需要与电脑网口保持同一网段
    ret = zcanlib.ZCAN_SetValue(device_handle, "0/ip", "192.168.1.253".encode("utf-8"))
    if ret == ZCAN_STATUS_ERR:
        print("设置目标IP失败！")

    chn_init_cfg = ZCAN_CHANNEL_INIT_CONFIG()
    memset(addressof(chn_init_cfg), 0, sizeof(chn_init_cfg))

    for i in range(can_number):
        ret = zcanlib.ZCAN_SetValue(device_handle, str(i) + "/set_device_tx_echo","1".encode("utf-8"))  # 发送回显设置，0-禁用，1-开启
        if ret != ZCAN_STATUS_OK:
            print("Set CH%d  set_device_tx_echo failed! 跳过该通道" % (i))
            continue

        chn_handle = zcanlib.InitCAN(device_handle, i, chn_init_cfg)
        if chn_handle is None:
            print("启动通道%d失败！跳过该通道" % i)
            continue
        print("通道句柄: %d." % chn_handle)

        ret = zcanlib.StartCAN(chn_handle)
        if ret == ZCAN_STATUS_ERR:
            print(f"StartCAN{i} fail, 跳过该通道")
            continue
        chn_handles.append(chn_handle)  # 将通道句柄添加到列表中

    if len(chn_handles) == 0:
        print("start_client_only: 没有成功初始化任何通道！")
        return None, None

    device_handles.append(device_handle)

    Read_Device_Info(device_handle)

    return device_handles, chn_handles

# 接收线程
def receive_thread(device_handle, chn_handle, chn_index, dbc_messages):
    global g_can0_512_watchdog

    # 方便打印对齐 --无实际作用
    CANType_width = len("CANFD加速    ")
    id_width = len(hex(0x1FFFFFFF))

    while thread_flag:
        time.sleep(0.005)
        rcv_num = zcanlib.GetReceiveNum(chn_handle, ZCAN_TYPE_CAN)  # CAN
        if rcv_num:
            if rcv_num > 100:
                rcv_msg, rcv_num = zcanlib.Receive(chn_handle, 100, 100)
            else:
                rcv_msg, rcv_num = zcanlib.Receive(chn_handle, rcv_num, 100)
            with print_lock:
                for msg in rcv_msg[:rcv_num]:
                    if chn_index == 1:
                        frame = msg.frame
                        raw_id = frame.can_id & 0x1FFFFFFF
                        print(f"[RAW-CAN1] ID=0x{raw_id:03X} dlc={frame.can_dlc}", flush=True)
                    frame = msg.frame
                    can_id = frame.can_id & 0x1FFFFFFF
                    if frame.can_id & (1 << 30):
                        dlc = 0
                        data_bytes = b""
                    else:
                        dlc = int(frame.can_dlc)
                        data_bytes = bytes(frame.data[:dlc])

                    # CAN2 收到 ID=0x512 → 保存原始数据，供定时发送线程使用
                    if chn_index == 2 and can_id == 1298 and dlc > 0:
                        with g_last_512_data_lock:
                            for j in range(min(dlc, 8)):
                                g_last_512_data[j] = frame.data[j]
                        # 收到 CAN2 首帧 0x512 → 置位事件，唤醒定时发送线程
                        g_can2_512_first_event.set()

                    # CAN0 收到 ID=0x512 → 清零看门狗计数器（回显正常）
                    if chn_index == 0 and can_id == 1298:
                        with g_can0_512_watchdog_lock:
                            g_can0_512_watchdog = 0

                    dbc_msg, decoded = decode_dbc_signals(dbc_messages, can_id, data_bytes)
                    if dbc_msg is not None:
                        # 通道校验：仅配置表允许的通道才存储并打印
                        allowed = CAN_MSG_CHANNEL_MAP.get(can_id)
                        if allowed is None or chn_index in allowed:
                            with g_latest_decoded_signals_lock:
                                g_latest_decoded_signals[can_id] = {k: v for k, (v, _) in decoded.items()}
                            # 打印所有已积累的信号值
                            with g_latest_decoded_signals_lock:
                                all_sigs = {k: v for d in g_latest_decoded_signals.values() for k, v in d.items()}
                            sig_text = " ".join([f"{k}={v:g}" for k, v in all_sigs.items()])
                            # print(
                            #     f"[{msg.timestamp}] CAN{chn_index} ID: {hex(can_id):<{id_width}} {dbc_msg.name} DLC:{dlc} {sig_text}"
                            # )

        rcv_canfd_num = zcanlib.GetReceiveNum(chn_handle, ZCAN_TYPE_CANFD)  # CANFD
        if rcv_canfd_num:
            if rcv_canfd_num > 100:
                rcv_canfd_msgs, rcv_canfd_num = zcanlib.ReceiveFD(chn_handle, 100, 100)
            else:
                rcv_canfd_msgs, rcv_canfd_num = zcanlib.ReceiveFD(chn_handle, rcv_canfd_num, 100)
            with print_lock:
                for msg in rcv_canfd_msgs[:rcv_canfd_num]:
                    if chn_index == 1:
                        frame = msg.frame
                        raw_id = frame.can_id & 0x1FFFFFFF
                        print(f"[RAW-CAN1-CANFD] ID=0x{raw_id:03X} dlc={frame.len}", flush=True)
                    frame = msg.frame
                    can_id = frame.can_id & 0x1FFFFFFF
                    dlc = int(frame.len)
                    data_bytes = bytes(frame.data[:dlc])
                    dbc_msg, decoded = decode_dbc_signals(dbc_messages, can_id, data_bytes)
                    if dbc_msg is not None:
                        # 通道校验：仅配置表允许的通道才存储并打印
                        allowed = CAN_MSG_CHANNEL_MAP.get(can_id)
                        if allowed is None or chn_index in allowed:
                            with g_latest_decoded_signals_lock:
                                g_latest_decoded_signals[can_id] = {k: v for k, (v, _) in decoded.items()}
                            # 打印所有已积累的信号值
                            with g_latest_decoded_signals_lock:
                                all_sigs = {k: v for d in g_latest_decoded_signals.values() for k, v in d.items()}
                            sig_text = " ".join([f"{k}={v:g}" for k, v in all_sigs.items()])
                            print(
                                f"[{msg.timestamp}] CAN{chn_index} ID: {hex(can_id):<{id_width}} {dbc_msg.name} DLC:{dlc} {sig_text}"
                            )

        rcv_merge_num = zcanlib.GetReceiveNum(device_handle, ZCAN_TYPE_MERGE)  # CANFD
        if rcv_merge_num:
            if rcv_merge_num > 100:
                rcv_merger_msgs, rcv_merge_num = zcanlib.ReceiveData(device_handle, 100, 100)
            else:
                rcv_merger_msgs, rcv_merge_num = zcanlib.ReceiveData(device_handle, rcv_merge_num, 100)
            with print_lock:
                for msg in rcv_merger_msgs[:rcv_merge_num]:
                    if msg.dataType == ZCAN_DT_ZCAN_CAN_CANFD_DATA:
                        frame = msg.data.zcanfddata.frame
                        if msg.chnl == 1:
                            raw_id = frame.can_id & 0x1FFFFFFF
                            print(f"[RAW-MERGE-CAN1] ID=0x{raw_id:03X} dlc={frame.len}", flush=True)
                        can_id = frame.can_id & 0x1FFFFFFF
                        dlc = int(frame.len)
                        data_bytes = bytes(frame.data[:dlc])
                        dbc_msg, decoded = decode_dbc_signals(dbc_messages, can_id, data_bytes)
                        if dbc_msg is not None:
                            # 通道校验：仅配置表允许的通道才存储并打印
                            allowed = CAN_MSG_CHANNEL_MAP.get(can_id)
                            if allowed is None or msg.chnl in allowed:
                                with g_latest_decoded_signals_lock:
                                    g_latest_decoded_signals[can_id] = {k: v for k, (v, _) in decoded.items()}
                                # 打印所有已积累的信号值
                                with g_latest_decoded_signals_lock:
                                    all_sigs = {k: v for d in g_latest_decoded_signals.values() for k, v in d.items()}
                                sig_text = " ".join([f"{k}={v:g}" for k, v in all_sigs.items()])
                                print(
                                    f"[{msg.data.zcanfddata.timestamp}] CAN{msg.chnl} ID: {hex(can_id):<{id_width}} {dbc_msg.name} DLC:{dlc} {sig_text}"
                                )
    print("=====")

# 设置滤波  白名单过滤(只接收范围内的数据)    CANFDNET设备支持设置最多16组滤波   -- 只有CANFDNET-400U支持
def Set_Filter(device_handle,chn):

    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/filter_clear", "0".encode("utf-8")) # 清除滤波
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d  filter_clear failed!" % chn)
        return None

    # 流程为：for【set_mode(与前一组滤波同类型可以省略) + set_start + set_end】+ ack
    # 这里设置第一道滤波：标准帧0~0x7F，第二道滤波：标准帧0xFF~0x1FF，第三道滤波：扩展帧0xFF~0x2FF
    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/filter_mode", "0".encode("utf-8"))  # 设置滤波模式 标准帧滤波
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d  filter_mode failed!" % chn)
        return None

    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/filter_start", "0".encode("utf-8")) # 设置白名单范围 起始ID
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d  filter_start failed!" % chn)
        return None

    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/filter_end", "0x7F".encode("utf-8"))   # 设置白名单范围 结束ID
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d  filter_end failed!" % chn)
        return None

    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/filter_start", "0xFF".encode("utf-8"))
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d  filter_start failed!" % chn)
        return None

    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/filter_end", "0x1FF".encode("utf-8"))
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d  filter_end failed!" % chn)
        return None

    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/filter_mode", "1".encode("utf-8"))  # 扩展帧滤波
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d  filter_mode failed!" % chn)
        return None

    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/filter_start", "0xFF".encode("utf-8"))
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d  filter_start failed!" % chn)
        return None

    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/filter_end", "0x2FF".encode("utf-8"))
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d  filter_end failed!" % chn)
        return None

    ret = zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/filter_ack", "0".encode("utf-8"))  # 使能滤波
    if ret != ZCAN_STATUS_OK:
        print("Set CH%d  filter_ack failed!" % chn)
        return None

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
    # 打印请求值
    print(f"[CANFDNET] 0x512 请求更新: ENGY_MODE_REQ={engy_mode}, DYN_MODE_REQ={dyn_mode}")

def get_all_signals():
    """
    统一外部读取接口：返回所有 DBC 信号物理值 + 看门狗故障状态。

    返回格式:
    {
        "STDE_DRV_ENGY_MODE_REQ": 3,
        "STDE_DRV_DYN_MODE_REQ": 5,
        "HV_BATT_SOC": 85.3,
        ...
        "_watchdog_fault": False,
    }

    仅包含已收到且通道匹配的报文信号，未收到的信号不会出现。
    """
    result = {}

    with g_latest_decoded_signals_lock:
        for decoded in g_latest_decoded_signals.values():
            for sig_name, value in decoded.items():
                result[sig_name] = value

    with g_can0_512_watchdog_lock:
        result["_watchdog_fault"] = g_can0_512_watchdog > CAN0_512_WATCHDOG_MAX

    return result


# ====== 旧版：事件驱动发送（已注释，保留备用）======
# def send_v2_bsi_512_thread(chn_handle, chn_index, dbc_messages):
#     """
#     事件驱动发送 V2_BSI_512 (ID: 0x512=1298) 报文线程。
#     CAN2 收到 0x512 后触发一次发送，最小发送间隔由 V2_BSI_512_MIN_INTERVAL_MS 控制 (默认10ms)。
#     以 CAN2 收到的原始 0x512 数据为基准，通过 cantools 解码后仅覆盖两个信号，
#     再重新编码发送，其他信号/数据位完整保留。
#
#     发送是否成功由诊断线程通过 CAN0 回显独立判断，本线程不参与诊断。
#     """
#     global g_can0_512_watchdog
#     v2_bsi_512_id = 1298  # 0x512
#     msg_def = dbc_messages.get_message_by_frame_id(v2_bsi_512_id)
#     roll_counter = 0
#     min_interval_sec = V2_BSI_512_MIN_INTERVAL_MS / 1000.0
#     last_send_time = 0.0
#
#     can_msgs = (ZCAN_Transmit_Data * 1)()
#     can_msgs[0].transmit_type = 0
#     can_msgs[0].frame.can_id = v2_bsi_512_id
#     can_msgs[0].frame.can_dlc = 8
#
#     while thread_flag:
#         # 等待 CAN2 收到 0x512 事件，超时则跳过本次发送
#         if not g_can2_512_event.wait(timeout=0.5):
#             continue
#         g_can2_512_event.clear()
#
#         if not thread_flag:
#             break
#
#         # 频率限制：确保两次发送间隔不小于 V2_BSI_512_MIN_INTERVAL_MS
#         now = time.time()
#         elapsed = now - last_send_time
#         if elapsed < min_interval_sec:
#             time.sleep(min_interval_sec - elapsed)
#
#         # 先拷贝 CAN2 原始全部 8 字节，保留所有非信号数据位
#         with g_last_512_data_lock:
#             for j in range(8):
#                 can_msgs[0].frame.data[j] = g_last_512_data[j]
#
#         if g_use_rolling_values:
#             engy_val = roll_counter & 0x7
#             dyn_val = (roll_counter >> 1) & 0x7
#         else:
#             engy_val = g_engy_mode_req
#             dyn_val = g_dyn_mode_req
#
#         # 用 encode 生成正确的信号值，仅覆盖信号所在 bit 位
#         encoded = msg_def.encode({
#             'TBD': 0,
#             'STDE_DRV_ENGY_MODE_REQ': engy_val,
#             'STDE_DRV_DYN_MODE_REQ': dyn_val,
#         })
#         # 打印实际发送的字节
#         print(f"[CAN-TX-last] 0x512: (ENGY_REQ={engy_val}, DYN_REQ={dyn_val})")
#
#         # byte0: bit5~bit7 覆盖为 DYN_MODE_REQ，bit2~bit4 覆盖为 ENGY_MODE_REQ，bit0~bit1 保留 CAN2 原始数据
#         can_msgs[0].frame.data[0] = (encoded[0] & 0xFC) | (can_msgs[0].frame.data[0] & 0x03)
#         # byte1及后面的数据: 完整复制 CAN2 原始数据，不做任何信号覆盖
#
#         zcanlib.Transmit(chn_handle, can_msgs, 1)
#         last_send_time = time.time()
#
#         # 看门狗：每发送一帧，计数器加1
#         if chn_index == 0:
#             with g_can0_512_watchdog_lock:
#                 g_can0_512_watchdog += 1
#
#         roll_counter = (roll_counter + 1) % 16
# ====== 旧版结束 ======


def send_v2_bsi_512_thread(chn_handle, chn_index, dbc_messages):
    """
    定时发送 V2_BSI_512 (ID: 0x512=1298) 报文线程。
    每隔 V2_BSI_512_MIN_INTERVAL_MS (默认100ms) 自动发送一帧，不依赖外部触发。
    以 CAN2 收到的原始 0x512 数据为基准，通过 cantools 解码后仅覆盖两个信号，
    再重新编码发送，其他信号/数据位完整保留。
    没有 CAN2 数据时，用全 0 作为模板发送。

    发送是否成功由诊断线程通过 CAN0 回显独立判断，本线程不参与诊断。
    """
    global g_can0_512_watchdog
    v2_bsi_512_id = 1298  # 0x512
    msg_def = dbc_messages.get_message_by_frame_id(v2_bsi_512_id)
    roll_counter = 0
    send_interval_sec = V2_BSI_512_MIN_INTERVAL_MS / 1000.0

    can_msgs = (ZCAN_Transmit_Data * 1)()
    can_msgs[0].transmit_type = 0
    can_msgs[0].frame.can_id = v2_bsi_512_id
    can_msgs[0].frame.can_dlc = 8

    # 阻塞等待 CAN2 首次收到 0x512 后，才开始定时发送
    print("[CANFDNET] 0x512 发送线程已就绪，等待 CAN2 首次收到 0x512 ...", flush=True)
    g_can2_512_first_event.wait()

    while thread_flag:
        time.sleep(send_interval_sec)          # 定时 sleep，不等外部事件

        if not thread_flag:
            break

        # 512 发送总开关：关闭时跳过本次发送，也不累加看门狗
        if not g_512_tx_enabled:
            roll_counter = (roll_counter + 1) % 16
            continue

        # 先拷贝 CAN2 原始全部 8 字节，保留所有非信号数据位（无数据时为全0）
        with g_last_512_data_lock:
            for j in range(8):
                can_msgs[0].frame.data[j] = g_last_512_data[j]

        if g_use_rolling_values:
            engy_val = roll_counter & 0x7
            dyn_val = (roll_counter >> 1) & 0x7
        else:
            engy_val = g_engy_mode_req
            dyn_val = g_dyn_mode_req

        # STDE_MODE_REQ_BIT_TGL：任一模式指令（ENGY/DYN）变化时翻转
        global g_last_sent_engy, g_last_sent_dyn, g_mode_req_bit_tgl
        with g_mode_req_bit_tgl_lock:
            if g_last_sent_engy is not None and g_last_sent_dyn is not None:
                if engy_val != g_last_sent_engy or dyn_val != g_last_sent_dyn:
                    g_mode_req_bit_tgl ^= 1
            g_last_sent_engy = engy_val
            g_last_sent_dyn = dyn_val
            tgl_val = g_mode_req_bit_tgl

        # 用 encode 生成正确的信号值，仅覆盖信号所在 bit 位
        encoded = msg_def.encode({
            'TBD': 0,
            'STDE_DRV_ENGY_MODE_REQ': engy_val,
            'STDE_DRV_DYN_MODE_REQ': dyn_val,
            'STDE_MODE_REQ_BIT_TGL': tgl_val,
        })
        # 打印实际发送的字节
        # print(f"[CAN-TX] 0x512: ENGY_REQ={engy_val}, DYN_REQ={dyn_val}")

        # byte0: bit5~bit7=DYN，bit2~bit4=ENGY，bit1=STDE_MODE_REQ_BIT_TGL，bit0 保留 CAN2 原始数据（TBD）
        can_msgs[0].frame.data[0] = (encoded[0] & 0xFE) | (can_msgs[0].frame.data[0] & 0x01)
        # byte1 及之后的字节: 完整复制 CAN2 原始数据，不做任何信号覆盖
        # （TGL 已移到 byte0 bit1，原 byte1 bit0 的覆盖逻辑已删除）

        # 打印最后发送完整 8 字节报文（HEX）
        data_hex = " ".join([f"{can_msgs[0].frame.data[i]:02X}" for i in range(8)])
        print(f"[CAN-TX] 0x512: {data_hex}  (ENGY_REQ={engy_val}, DYN_REQ={dyn_val})")

        zcanlib.Transmit(chn_handle, can_msgs, 1)

        # 看门狗：每发送一帧，计数器加1
        if chn_index == 0:
            with g_can0_512_watchdog_lock:
                g_can0_512_watchdog += 1

        roll_counter = (roll_counter + 1) % 16


def diag_can0_512_thread():
    """
    看门狗诊断线程（5Hz）：检查 g_can0_512_watchdog 计数器。
    发送线程每发一帧 +1，接收线程收到 CAN0 回显时清零。
    计数器超过 CAN0_512_WATCHDOG_MAX (50帧=5秒) 则报发送中断。
    完全无锁竞争：仅读取计数器，不依赖时间戳。
    """
    DIAG_PERIOD = 0.2  # 5Hz
    warned = False

    while thread_flag:
        time.sleep(DIAG_PERIOD)
        with g_can0_512_watchdog_lock:
            cnt = g_can0_512_watchdog

        if cnt > CAN0_512_WATCHDOG_MAX:
            if not warned:
                print(f"[DIAG] CAN0 0x512 发送中断！看门狗计数={cnt} (>阈值{CAN0_512_WATCHDOG_MAX})", flush=True)
                # 把改写值归零，相当于"不干预"
                # global g_engy_mode_req, g_dyn_mode_req
                # g_engy_mode_req = 0
                # g_dyn_mode_req = 0
                warned = True
        else:
            if warned:
                print(f"[DIAG] CAN0 0x512 发送已恢复（看门狗计数={cnt}）", flush=True)
                warned = False

# ==================== 公开接口 ====================

def init_can():
    """初始化 CAN 设备并启动所有后台线程。返回 True/False。"""
    global zcanlib, g_dbc_messages, g_device_handles, g_chn_handles, g_threads, thread_flag

    thread_flag = True
    zcanlib = ZCAN()
    g_dbc_messages = load_dbc(dbc_path)

    # PC作为一个客户端，去连接CANFDNET服务器 (4通道)
    handles, chn_handles = start_client_only(4)
    if handles is None or chn_handles is None:
        print("CAN 初始化失败！")
        return False

    g_device_handles = handles
    g_chn_handles = chn_handles
    g_threads = []

    # 每个通道各自开一个接收线程
    if len(handles) == 1:
        for j in range(len(chn_handles)):
            t = threading.Thread(target=receive_thread, args=(handles[0], chn_handles[j], j, g_dbc_messages))
            g_threads.append(t)
            t.start()
    else:
        for i in range(len(handles)):
            t = threading.Thread(target=receive_thread, args=(handles[i], chn_handles[i], i, g_dbc_messages))
            g_threads.append(t)
            t.start()

    # 重置首帧事件，确保发送线程等待 CAN2 首次收到 0x512 后才开始
    g_can2_512_first_event.clear()

    # 启动V2_BSI_512事件驱动发送线程 (CAN2收到0x512触发, 最小间隔10ms)
    t_send = threading.Thread(target=send_v2_bsi_512_thread, args=(chn_handles[0], 0, g_dbc_messages))
    g_threads.append(t_send)
    t_send.start()

    # 启动CAN0 0x512诊断线程 (独立监控发送阻塞和回显)
    t_diag = threading.Thread(target=diag_can0_512_thread, daemon=True)
    t_diag.start()

    print("CAN 设备已启动，4通道接收中...")
    return True


def shutdown_can():
    """安全关闭所有 CAN 线程、通道和设备。"""
    global thread_flag
    thread_flag = False

    # 等待所有接收/发送线程结束
    for t in g_threads:
        t.join(timeout=3)

    # 复位通道
    for i, ch in enumerate(g_chn_handles):
        ret = zcanlib.ResetCAN(ch)
        if ret == 1:
            print(f"关闭通道{i}成功")

    # 关闭设备
    for h in g_device_handles:
        ret = zcanlib.CloseDevice(h)
        if ret == 1:
            print("关闭设备成功")


# ==================== 自发自收测试模式（利用 TX Echo） ====================
# 说明：
#   init_can() 中已为每个通道开启 TX Echo（发送回显），即同一 TCP 连接内发送的 CAN
#   报文会被接收线程收到并解码。本模块利用此特性，在无实车 / 无外部 CAN 信号源的
#   条件下，由代码自己周期发送测试报文到 CAN1/CAN2，形成自发自收闭环，用于验证
#   整条数据链路是否正常：
#
#     发送线程 → CANFDNET → TX Echo → 接收线程 → DBC解码 → g_latest_decoded_signals
#         ↑                                                             ↓
#         └──────────────── main.py ← get_all_signals() ←───────────────┘
#
#   接真车时只需注释掉 start_test_tx() 调用即可，其余代码无需任何修改。
# ========================================================================

import random as _random

# 测试报文配置：(通道索引, CAN ID hex, {信号名: 值, ...})
# 信号值使用 DBC 原始值（物理值），由 cantools.encode() 自动编码为 8 字节数据
_TEST_MSGS_CONFIG = [
    # CAN2: 0x228 CMM_228 — 油门踏板 25%（借道 CAN2: CAN1 物理总线无 ACK，TX Echo 不生效）
    (2, 0x228, {"EFCMNT_PDLE_ACCEL_228": 25.0}),
    # CAN2: 0x5E2 VCU_5E2 — EV 模式 + 标准驾驶
    (2, 0x5E2, {"STDE_DRV_ENGY_MODE_STATE": 0, "STDE_DRV_DYN_MODE_STATE": 1}),
    # CAN2: 0x5A2 VCU_5A2 — SOC 78.5%
    (2, 0x5A2, {"HV_BATT_SOC": 78.5}),
    # CAN2: 0x4FE ESM_4FE — D 档 (3)
    (2, 0x4FE, {"POS_MONOSTABLE_LEVER": 3}),
    # CAN2: 0x522 E_VCU_522 — 电池温度 35°C
    (2, 0x522, {"HV_BATT_TEMP_AVG": 35.0}),
    # CAN2: 0x38D ABR_38D — 车速 50 km/h
    (2, 0x38D, {"VITESSE_VEHICULE_ROUES": 50.0}),
    # CAN2: 0x4D8 DAT_CMM_4D8 — 电池电流 10.0A，电压 380.0V
    (2, 0x4D8, {"HV_BATT_REAL_CURR_HD": 10.0, "HV_BATT_REAL_VOLT_HD": 380.0}),
]

TEST_TX_PERIOD = 0.5          # 发送周期 (秒)，100ms = 10Hz
g_test_tx_active = False      # 测试发送开关
g_test_tx_threads = []        # 发送线程列表


def _encode_test_data(can_id, signals_values):
    """使用 DBC 定义编码信号值为 8 字节 CAN 数据（未定义的信号填 0）。"""
    msg_def = g_dbc_messages.get_message_by_frame_id(can_id)
    # 收集该报文的所有信号名，缺失的填 0
    full_signals = {}
    for sig in msg_def.signals:
        full_signals[sig.name] = signals_values.get(sig.name, 0)
    encoded = msg_def.encode(full_signals)
    # 补齐到 8 字节
    return bytes(encoded).ljust(8, b'\x00')[:8]


def _test_tx_thread(chn_handle, can_id, encoded_data):
    """后台线程：周期性地向指定通道发送测试 CAN 报文。"""
    can_msgs = (ZCAN_Transmit_Data * 1)()
    memset(can_msgs, 0, sizeof(can_msgs))
    can_msgs[0].frame.can_id = can_id
    can_msgs[0].frame.can_dlc = len(encoded_data)
    for j, b in enumerate(encoded_data):
        can_msgs[0].frame.data[j] = b

    while g_test_tx_active:
        zcanlib.Transmit(chn_handle, can_msgs, 1)
        time.sleep(TEST_TX_PERIOD)


def start_test_tx():
    """
    [自发自收测试] 启动测试报文发送。
    向 CAN1/CAN2 周期发送模拟报文，利用已开启的 TX Echo 回显后由接收线程解码。
    调用前需确保 init_can() 已成功运行。
    返回 True/False。
    """
    global g_test_tx_active, g_test_tx_threads

    if g_test_tx_active:
        print("[TestTx] 测试模式已启动，无需重复启动")
        return True

    if not g_chn_handles or len(g_chn_handles) < 3:
        print("[TestTx] 错误：CAN 通道未就绪，请先调用 init_can()")
        return False

    g_test_tx_active = True
    g_test_tx_threads = []

    for chn_idx, can_id, sigs in _TEST_MSGS_CONFIG:
        # 用 DBC 自动编码，保证数据格式绝对正确
        encoded = _encode_test_data(can_id, sigs)
        t = threading.Thread(
            target=_test_tx_thread,
            args=(g_chn_handles[chn_idx], can_id, encoded),
            daemon=True,
        )
        g_test_tx_threads.append(t)
        t.start()
        sig_desc = ", ".join(f"{k}={v}" for k, v in sigs.items())
        print(f"[TestTx] CAN{chn_idx} → 周期发送 0x{can_id:03X} ({sig_desc})")

    print("[TestTx] 全部测试报文已启动，TX Echo 回显后自动解码")
    return True


def stop_test_tx():
    """[自发自收测试] 停止测试报文发送。"""
    global g_test_tx_active
    g_test_tx_active = False
    for t in g_test_tx_threads:
        t.join(timeout=2)
    g_test_tx_threads.clear()
    print("[TestTx] 已停止")
# ==================== 自发自收测试模式 结束 ====================


# ==================== Mock 模式（无需 CAN 硬件） ====================
g_mock_tx_active = False
g_mock_tx_thread = None


def init_can_mock():
    """
    [Mock] 初始化 Mock CAN 模式：仅加载 DBC，不连接任何硬件。
    接口与 init_can() 一致，返回 True/False。
    """
    global g_dbc_messages
    g_dbc_messages = load_dbc(dbc_path)
    print("[MockCAN] DBC 已加载，Mock 模式就绪（无需CAN硬件）")
    return True


def _mock_inject_thread():
    """
    [Mock] 后台线程：周期注入测试信号到 g_latest_decoded_signals。
    直接写入字典，无需编码/解码/发送，完全绕过硬件。
    """
    while g_mock_tx_active:
        with g_latest_decoded_signals_lock:
            for chn_idx, can_id, sigs in _TEST_MSGS_CONFIG:
                g_latest_decoded_signals[can_id] = dict(sigs)
        time.sleep(TEST_TX_PERIOD)


def start_mock_test_tx():
    """
    [Mock] 启动模拟信号注入线程。
    接口与 start_test_tx() 一致，返回 True/False。
    """
    global g_mock_tx_active, g_mock_tx_thread

    if g_mock_tx_active:
        print("[MockCAN] 已在运行中")
        return True

    if g_dbc_messages is None:
        print("[MockCAN] 错误：DBC未加载，请先调用 init_can_mock()")
        return False

    g_mock_tx_active = True
    g_mock_tx_thread = threading.Thread(
        target=_mock_inject_thread,
        daemon=True,
    )
    g_mock_tx_thread.start()

    # 打印测试信号概览
    for chn_idx, can_id, sigs in _TEST_MSGS_CONFIG:
        sig_desc = ", ".join(f"{k}={v}" for k, v in sigs.items())
        print(f"[MockCAN] → 0x{can_id:03X} ({sig_desc})")
    print("[MockCAN] 信号注入已启动（周期 %.1fs）" % TEST_TX_PERIOD)
    return True


def stop_mock_test_tx():
    """[Mock] 停止模拟信号注入线程。"""
    global g_mock_tx_active
    g_mock_tx_active = False
    if g_mock_tx_thread:
        g_mock_tx_thread.join(timeout=2)
    print("[MockCAN] 已停止")


def shutdown_can_mock():
    """[Mock] 关闭 Mock CAN 模式。"""
    stop_mock_test_tx()
    print("[MockCAN] 已关闭")
# ==================== Mock 模式结束 ====================


if __name__ == "__main__":
    if init_can():
        try:
            while True:
                sigs = get_all_signals()
                print(f"当前信号: {sigs}")
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            shutdown_can()
