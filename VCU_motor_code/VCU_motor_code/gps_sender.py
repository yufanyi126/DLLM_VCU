# coding:UTF-8
"""
    车载端 GPS 数据发送程序

    运行在车载设备（树莓派/工控机）上，负责：
    1. 从串口读取 WT901C485 传感器数据
    2. 通过 TCP 发送到 main.py 所在电脑的 NetworkReader

    用法:
        python gps_sender.py

    配置修改下方常量即可：
        - MAIN_IP:    运行 main.py 的电脑 IP
        - MAIN_PORT:  main.py 的监听端口
        - SERIAL_PORT: GPS 传感器串口路径
"""

import sys
import os
import socket
import json
import time
import platform

# ============ 路径配置 ============
_BASE = os.path.dirname(__file__)

# sensor_reader 所在的 chs 目录
_CHS_DIR = os.path.join(
    _BASE,
    'WitStandardModbus_WT901C485-main', 'Python', 'Python-SDK-WT901C485', 'chs'
)
sys.path.insert(0, _CHS_DIR)

from sensor_reader import SensorReader

# ============ 配置 ============
# 运行 main.py 的电脑 IP 地址
MAIN_IP = '10.194.1.183'
# main.py NetworkReader 监听端口
MAIN_PORT = 9999
MAIN_IP = os.environ.get('VCU_MAIN_IP', MAIN_IP)
# GPS 传感器串口路径
SERIAL_PORT = '/dev/ttyUSB0' if platform.system().lower() == 'linux' else 'COM7'
# GPS 波特率
BAUDRATE = 9600
# 发送间隔（秒），建议比 main.py step() 的循环间隔小
SEND_INTERVAL = 0.5
# 重连间隔（秒），连接断开后等待多久重试
RECONNECT_INTERVAL = 3.0
# =============================


def main():
    print(f"=" * 50)
    print(f"  车载端 GPS 发送程序")
    print(f"  目标: {MAIN_IP}:{MAIN_PORT}")
    print(f"  串口: {SERIAL_PORT}@{BAUDRATE}")
    print(f"  发送间隔: {SEND_INTERVAL}s")
    print(f"=" * 50)

    # ---------- 打开传感器 ----------
    print(f"\n[信息] 正在打开传感器 {SERIAL_PORT} ...")
    try:
        reader = SensorReader(port=SERIAL_PORT, baud=BAUDRATE)
        reader.open()
    except Exception as e:
        print(f"[错误] 传感器初始化失败: {e}")
        return
    print("[信息] 传感器已就绪")

    sock = None

    try:
        while True:
            # ===== 1. 从传感器读取数据 =====
            data = reader.get_data()
            if not data:
                time.sleep(SEND_INTERVAL)
                continue

            # 只发 GPS 有效的数据（至少要有经纬度）
            lat = data.get('latitude')
            lon = data.get('longitude')
            if lat is None or lon is None:
                time.sleep(SEND_INTERVAL)
                continue

            # ===== 2. 确保 socket 已连接 =====
            while sock is None:
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(5.0)
                    sock.connect((MAIN_IP, MAIN_PORT))
                    print(f"[信息] 已连接 main.py ({MAIN_IP}:{MAIN_PORT})")
                except Exception as e:
                    print(f"[警告] 连接失败: {e}，{RECONNECT_INTERVAL}s 后重试...")
                    if sock:
                        sock.close()
                        sock = None
                    time.sleep(RECONNECT_INTERVAL)

            # ===== 3. 发送数据 =====
            try:
                # 序列化为 JSON，每帧一行
                payload = json.dumps(data, ensure_ascii=False) + '\n'
                sock.sendall(payload.encode('utf-8'))
                print(f"[发送] 经度={lon:.6f} 纬度={lat:.6f} 地速={data.get('GroundSpeed', 0):.1f}km/h")
            except (socket.error, BrokenPipeError, ConnectionResetError) as e:
                print(f"[警告] 发送失败: {e}，准备重连...")
                sock.close()
                sock = None
                continue

            time.sleep(SEND_INTERVAL)

    except KeyboardInterrupt:
        print("\n[信息] 用户中断")
    finally:
        if sock:
            sock.close()
        reader.close()
        print("[信息] 已退出")


if __name__ == '__main__':
    main()
