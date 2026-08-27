# coding:UTF-8
"""
    simulink_client.py — Simulink 仿真数据源客户端
    =================================================
    用于替代真实 CAN 盒，从 Simulink 仿真模型获取车端信号数据。

    功能：
    1. 通过 TCP 连接 Simulink 模型，接收 8 个 double（64 字节）状态数据
    2. 将仿真数据转换为与真实 CAN 信号同名同结构的 dict
    3. 通过 TCP 将控制指令（engy_mode, dyn_mode）发送给 Simulink

    数据帧格式（与 simulink_code/code/simlink.py 一致）：
        接收帧（8 double, 64 字节）:
            [0] EFCMNT_PDLE_ACCEL       - 油门踏板开度 (%)
            [1] HV_BATT_TEMP_AVG        - 电池平均温度 (°C)
            [2] VITESSE_VEHICULE_ROUES  - 车速 (km/h)
            [3] POS_MONOSTABLE_LEVER    - 档位 (0=P, 1=R, 2=N, 3=D)
            [4] HV_BATT_SOC             - 电池 SOC (%)
            [5] STDE_DRV_DYN_MODE_STATE - 驾驶模式 (0=标准, 1=SPORT, 2=ECO)
            [6] STDE_DRV_ENGY_MODE_STATE- 能量模式 (0=HEV, 1=EV)
            [7] sim_time                - 仿真时间

        发送帧（2 double, 16 字节）:
            (dyn_mode, engy_mode)
"""

import struct
import socket
import threading
import time


# ===== 信号字段名列表（按 Simulink 帧解析顺序） =====
_SIGNAL_KEYS = [
    "EFCMNT_PDLE_ACCEL_228",     # 油门踏板开度（与真实 CAN 的 0x228 信号名对齐）
    "HV_BATT_TEMP_AVG",          # 电池平均温度
    "VITESSE_VEHICULE_ROUES",    # 车速
    "POS_MONOSTABLE_LEVER",      # 档位
    "HV_BATT_SOC",               # 电池 SOC
    "STDE_DRV_DYN_MODE_STATE",   # 驾驶模式
    "STDE_DRV_ENGY_MODE_STATE",  # 能量模式
    "_sim_time",                 # 仿真时间（内部使用）
]

# 真实 CAN 信号中，油门踏板有两个可能的信号名（0x228 / 0x278），
# 仿真模式下同时填充两者，保证 main.py 中的兼容性读取都能命中。
_THROTTLE_ALIAS_KEYS = ["EFCMNT_PDLE_ACCEL_228", "EFCMNT_PDLE_ACCEL_278"]


class SimulinkClient:
    """
    Simulink 仿真数据源客户端。

    用法:
        client = SimulinkClient(recv_host="127.0.0.1", recv_port=6000,
                                 send_host="127.0.0.1", send_port=7000)
        client.connect()
        # ... 循环中调用 ...
        signals = client.get_signals()
        client.set_drv_mode_values(engy_mode=1, dyn_mode=0)
        client.close()
    """

    def __init__(self, recv_host="127.0.0.1", recv_port=6000,
                 send_host="127.0.0.1", send_port=7000,
                 frame_size=64):
        self.recv_host = recv_host
        self.recv_port = recv_port
        self.send_host = send_host
        self.send_port = send_port
        self.frame_size = frame_size  # 8 * 8 字节

        self._recv_sock = None
        self._send_sock = None
        self._lock = threading.Lock()
        self._latest_signals = {}
        self._running = False
        self._recv_thread = None

    # ==================== 连接管理 ====================

    def connect(self):
        """建立与 Simulink 的 TCP 连接，启动后台接收线程"""
        # 接收 socket
        self._recv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._recv_sock.settimeout(5.0)
        try:
            self._recv_sock.connect((self.recv_host, self.recv_port))
            print(f"[Simulink] TCP 接收已连接: {self.recv_host}:{self.recv_port}")
        except Exception as e:
            print(f"[Simulink] TCP 接收连接失败 ({self.recv_host}:{self.recv_port}): {e}")
            self._recv_sock = None
            raise

        # 发送 socket
        self._send_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._send_sock.settimeout(5.0)
        try:
            self._send_sock.connect((self.send_host, self.send_port))
            print(f"[Simulink] TCP 发送已连接: {self.send_host}:{self.send_port}")
            # 发送初始默认值 (0, 0)
            self._send_packed(0.0, 0.0)
        except Exception as e:
            print(f"[Simulink] TCP 发送连接失败 ({self.send_host}:{self.send_port}): {e}")
            self._send_sock = None
            # 发送通道失败不影响接收通道继续工作
            print("[Simulink] 警告: 控制指令下发通道不可用，仿真模型将无法接收 AI 决策")

        # 启动后台接收线程
        self._running = True
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()
        print("[Simulink] 后台接收线程已启动")

    def close(self):
        """关闭所有连接"""
        self._running = False
        if self._recv_thread:
            self._recv_thread.join(timeout=3.0)
        if self._recv_sock:
            try:
                self._recv_sock.close()
            except Exception:
                pass
            self._recv_sock = None
        if self._send_sock:
            try:
                self._send_sock.close()
            except Exception:
                pass
            self._send_sock = None
        print("[Simulink] 连接已关闭")

    # ==================== 数据接收 ====================

    def _recv_loop(self):
        """后台线程：持续从 Simulink 接收数据帧"""
        buffer = b""
        while self._running:
            try:
                data = self._recv_sock.recv(1024)
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    print(f"[Simulink] 接收异常: {e}")
                break

            if not data:
                print("[Simulink] 对端关闭了连接")
                break

            buffer += data
            while len(buffer) >= self.frame_size:
                frame = buffer[:self.frame_size]
                buffer = buffer[self.frame_size:]
                self._parse_frame(frame)

    def _parse_frame(self, frame):
        """解析一帧 64 字节数据（8 个 double），更新最新信号值"""
        try:
            values = struct.unpack("<8d", frame)
        except struct.error:
            print(f"[Simulink] 帧解析失败，帧长={len(frame)}")
            return

        signals = {}
        for i, key in enumerate(_SIGNAL_KEYS):
            signals[key] = values[i]

        # 油门踏板值同时填充两个别名键
        throttle_val = values[0]
        for alias in _THROTTLE_ALIAS_KEYS:
            signals[alias] = throttle_val

        with self._lock:
            self._latest_signals = signals

    def get_signals(self):
        """
        获取最新的仿真信号数据。

        返回:
            dict: 与真实 CAN get_all_signals() 同结构的信号字典。
                  键名完全对齐真实 CAN 信号，可直接替代使用。
                  如果尚未收到任何数据，返回空 dict。
        """
        with self._lock:
            return dict(self._latest_signals)

    # ==================== 控制下发 ====================

    def _send_packed(self, dyn_mode, engy_mode):
        """通过 TCP 发送 2 个 double 给 Simulink"""
        if not self._send_sock:
            print("[Simulink] 发送失败: 发送通道未连接")
            return False
        try:
            packed = struct.pack("<dd", float(dyn_mode), float(engy_mode))
            self._send_sock.sendall(packed)
            return True
        except Exception as e:
            print(f"[Simulink] 发送异常: {e}")
            return False

    def set_drv_mode_values(self, engy_mode, dyn_mode):
        """
        将驾驶/能量模式控制指令下发给 Simulink。

        参数:
            engy_mode: 能量模式 (0=HEV, 1=EV)
            dyn_mode:  驾驶模式 (0=标准, 1=SPORT, 2=ECO)
        """
        ok = self._send_packed(dyn_mode, engy_mode)
        if ok:
            print(f"[Simulink] 已下发控制: engy_mode={engy_mode}, dyn_mode={dyn_mode}")
        return ok
