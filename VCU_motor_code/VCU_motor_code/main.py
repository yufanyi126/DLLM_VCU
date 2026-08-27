# coding:UTF-8
"""
    主程序：
        调用 GPS 传感器读取数据
        调用高德API获取起点终点道路和交通信息
        支持 GPS 丢失时的航位推算
        支持 API 失败时的缓存回退
"""
import sys
import os
import time
import json
import random
import re
import platform
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# ========== 路径配置 ==========
_BASE = os.path.dirname(__file__)

# 将 code/ 目录加入搜索路径（gaode_api, route_tracker）
sys.path.insert(0, os.path.join(_BASE, 'code'))

# 将 chs 目录加入搜索路径（sensor_reader）
_CHS_DIR = os.path.join(
    _BASE,
    'WitStandardModbus_WT901C485-main', 'Python', 'Python-SDK-WT901C485', 'chs'
)
sys.path.insert(0, _CHS_DIR)

# 将 CANLan 目录加入搜索路径（zlgcan, CANFDNET）
sys.path.insert(0, os.path.join(_BASE, 'CANLan'))

from sensor_reader import SensorReader
from gaode_api import (
    get_geocode, get_driving_direction,
    extract_route_sequences, build_route_steps,
    save_route_cache, load_route_cache,
    parse_driving_result,
)
from route_tracker import RouteTracker

# ===== 导入 CAN 模块 =====
from CANFDNET import (
    init_can, init_can_mock,
    shutdown_can, shutdown_can_mock,
    get_all_signals,
    set_drv_mode_values,
    start_test_tx, start_mock_test_tx,
    stop_test_tx, stop_mock_test_tx,
)


# ============ 配置 ============
API_REFRESH_INTERVAL = 120      # 设置高德API 路况刷新间隔（秒）
GPS_MIN_SATELLITES = 4         # GPS 有效所需最少卫星数
GPS_MAX_PDOP = 10.0             # GPS 有效所需最大 PDOP，PDOP 越小越精确

# ---------- 模拟车速配置 ----------
USE_SIMULATED_SPEED = False    # True=强制模拟车速, False=CAN车速优先 > GPS地速备选 > 无速度
SIMULATED_SPEEDS = [           # 模拟车速列表 (km/h)，会随机选一个
    50, 55, 60, 65, 70, 80, 90,
]
SPEED_CHANGE_INTERVAL = 5.0    # 每隔多少秒更换一次随机速度
# ============================

# ---------- HTTP 仪表盘开关 ----------
# True  = init_session 时启动 HTTP 后台服务（GPS 仪表盘 + API）
# False = 不启动 HTTP 服务，节省端口资源
ENABLE_HTTP_DASHBOARD = False
# ====================================

# ---------- 网络 GPS 开关 ----------
# True  = 通过网络 TCP 接收远程车载 GPS 数据（GPS 传感器不在本机）
# False = 通过本机串口直连 GPS 传感器（默认方式）
USE_NETWORK_GPS = False
NETWORK_GPS_HOST = '0.0.0.0'   # NetworkReader 本机监听地址
NETWORK_GPS_PORT = 9999         # NetworkReader 监听端口
# ====================================

# ---------- main 输出数据测试开关 ----------
# True 时不连接 GPS、CAN 或高德 API，直接向 car_server 提供固定的 output_data。
# 仅用于手动验证 main -> car_server -> receive_demo -> Agent 的完整链路。
# 完全不依赖任何硬件/网络/API，用硬编码的假数据跑通整条流程      0是关闭，1是开启
MAIN_TEST_DATA_SWITCH = os.environ.get("VCU_MAIN_TEST_DATA", "0").lower() in ("1", "true", "yes", "on")
# ====================================


def is_main_test_data_enabled():
    """运行时读取测试开关，保证 car_server 导入 main 后再设置开关也能生效。"""
    return os.environ.get("VCU_MAIN_TEST_DATA", str(MAIN_TEST_DATA_SWITCH)).lower() in (
        "1", "true", "yes", "on"
    )

# ---------- CAN 自发自收测试开关 ----------
# True  = 使用自发自收测试模式（无实车时自动注入模拟 CAN 报文）
# False = 使用真实 CAN 设备接收外部报文
USE_CAN_TEST_TX = False
# ==========================================

# ---------- CAN Mock 模式 ----------
# True  = 使用 Mock CAN（无需硬件，模拟信号注入）
# False = 使用真实 CAN 设备
MOCK_CAN = False
# ====================================

# ---------- Simulink 仿真数据源开关 ----------
# True  = 车端 CAN 数据从 Simulink 仿真模型获取（替代真实 CAN 盒）
# False = 车端 CAN 数据从真实 CAN 盒获取  False关闭仿真，True 启动仿真
USE_SIMULINK = True

# Simulink TCP 连接参数（与 simulink_code/code/simlink.py 保持一致）
SIMULINK_RECV_HOST = "127.0.0.1"
SIMULINK_RECV_PORT = 6000    # 从 Simulink 接收状态数据 (8 double, 64 字节)
SIMULINK_SEND_HOST = "127.0.0.1"
SIMULINK_SEND_PORT = 7000    # 向 Simulink 发送控制指令 (2 double)
# ==========================================

# ---------- 模拟 GPS 开关 ----------
# True  = 不初始化真实 GPS 传感器，step() 中用内置模拟 GPS 数据
# False = 通过串口/网络连接真实 GPS 传感器   False 关闭模拟， True 开启模拟
MOCK_GPS = True

# 模拟 GPS 数据（起点的附近坐标，用于路线跟踪）
MOCK_GPS_LAT = 29.7165
MOCK_GPS_LON = 106.8310
MOCK_GPS_SPEED = 45.0  # km/h
# ==============================

# ============ HTTP 共享数据 ============
_API_ROUTE_DATA = {}       # 路线数据（初始化后不变）
_API_STATUS_DATA = {        # 实时状态（主循环每次更新）
    'lat': None, 'lng': None, 'speed': 0,
    'progress_pct': 0.0, 'remaining_km': 0.0,
    'current_step': 0, 'total_steps': 0,
    'road_name': '', 'traffic': '',
    'gps_valid': False, 'finished': False,
    'gps_lost': False, 'api_offline': False, 'accumulated_dist': 0,
    'sat_count': 0, 'hdop': 0.0,
}

# ============ 历史数据缓冲区（用于 GPS 仪表盘曲线图） ============
_HISTORY_DATA = {
    'timestamps': [],
    'progress_pct': [],
    'remaining_km': [],
    'speed': [],
    'lat': [],
    'lng': [],
    'gps_valid': [],
    'api_refresh': [],        # 标记 API 路况刷新时间点
    'current_step': [],
    'traffic': [],
}
_MAX_HISTORY = 300  # 最多保留 300 条历史记录

_DASHBOARD_PATH = os.path.join(_BASE, 'gps_dashboard.html')
# =======================================


# ===== CAN 信号枚举翻译表 =====
# 驾驶模式 (STDE_DRV_DYN_MODE_STATE, 0x5E2)
# 基准: receive_demo.py decision_to_control() → 0=标准, 1=SPORT, 2=ECO
DYN_MODE_MAP = {
    0: "标准",
    1: "SPORT",
    2: "ECO",
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
    0: "HEV",
    1: "EV",
}


def build_test_output_data(frame_index=0):
    """构建可被 receive_demo 直接消费的测试 output_data，不依赖车辆硬件。"""
    # 每次调用切换当前路段，便于手动观察 Agent 对不同路段的返回结果。
    current_segment = 1 if frame_index % 2 == 0 else 2
    segments = [
        {
            "sequence": 1,
            "route": "测试城市道路",
            "instruction": "直行通过路口",
            "distance_m": 600,
            "remaining_distance_m": 420 if current_segment == 1 else 0,
            "traffic": "畅通",
            "status": "current" if current_segment == 1 else "completed",
            "speed": 35.0,
            "speed_source": "main_test_data",
            "driving": "标准",
            "engy_mode": "EV",
            "soc": "78.5%",
            "gear": "D",
            "throttle": "22.0%",
            "battery": "32C",
        },
        {
            "sequence": 2,
            "route": "测试快速路",
            "instruction": "保持直行",
            "distance_m": 1500,
            "remaining_distance_m": 1100 if current_segment == 2 else 1500,
            "traffic": "缓行",
            "status": "current" if current_segment == 2 else "pending",
            "speed": 78.0,
            "speed_source": "main_test_data",
            "driving": "标准",
            "engy_mode": "HEV",
            "soc": "76.8%",
            "gear": "D",
            "throttle": "38.0%",
            "battery": "33C",
        },
    ]
    return {
        "route_summary": {
            "total_distance_km": 2.1,
            "remaining_km": 1.52 if current_segment == 1 else 1.1,
            "progress_pct": 12.0 if current_segment == 1 else 48.0,
            "accumulated_dist_m": 250.0 if current_segment == 1 else 1000.0,
            "current_segment": current_segment,
        },
        "segments": segments,
    }


def translate_dyn_mode(value):
    """将驾驶模式值翻译为中文标签"""
    if value is None:
        return ""
    return DYN_MODE_MAP.get(int(value), f"未知({value})")


def translate_gear(value):
    """将档位值翻译为标签"""
    if value is None:
        return ""
    return GEAR_MAP.get(int(value), f"未知({value})")


def translate_engy_mode(value):
    """将能量模式值翻译为标签"""
    if value is None:
        return ""
    return ENGY_MODE_MAP.get(int(value), f"未知({value})")


def print_sensor_data(data):
    """
    打印传感器数据
    :param data: get_data() 返回的 dict
    """
    if not data:
        print("无数据")
        return

    # 第一行：芯片时间 + 温度 + 加速度 + 角速度 + 角度 + 磁场
    line1 = (
        f"芯片时间:{data.get('Chiptime', 'N/A')}"
        f"  温度:{data.get('temperature', 'N/A')}"
        f"  加速度:{data.get('accX', 0):.4f},{data.get('accY', 0):.4f},{data.get('accZ', 0):.4f}"
        f"  角速度:{data.get('gyroX', 0):.4f},{data.get('gyroY', 0):.4f},{data.get('gyroZ', 0):.4f}"
        f"  角度:{data.get('angleX', 0):.3f},{data.get('angleY', 0):.3f},{data.get('angleZ', 0):.3f}"
        f"  磁场:{data.get('magX', 0)},{data.get('magY', 0)},{data.get('magZ', 0)}"
    )
    print(line1)

    # 第二行：GPS 数据
    lon = data.get('longitude')
    lat = data.get('latitude')
    if lon is not None and lat is not None:
        line2 = (
            f" GPS: 经度={lon}° 纬度={lat}°"
            f" 高度={data.get('Height', 0)}m"
            f" 航向={data.get('Yaw', 0)}°"
            f" 地速={data.get('GroundSpeed', 0)}km/h"
            f" 卫星数={data.get('SatCount', 0)}"
            f" PDOP={data.get('PDOP', 0)}"
            f" HDOP={data.get('HDOP', 0)}"
        )
        print(line2)
    else:
        print(" GPS: 未定位")


def format_sensor_output(data):
    """
    将传感器数据格式化为 JSON 字符串（用于 HTTP 发送等场景）
    :param data: get_data() 返回的 dict
    :return: JSON 字符串
    """
    if not data:
        return "{}"

    cleaned = {}
    for key, value in data.items():
        if isinstance(value, float):
            cleaned[key] = round(value, 6)
        else:
            cleaned[key] = value

    return json.dumps(cleaned, indent=2, ensure_ascii=False)


def is_coordinate(text):
    """
    判断字符串是否为纯坐标格式 'lng,lat'（如 '106.830767,29.716379'）
    """
    if not isinstance(text, str):
        return False
    pattern = r'^\s*-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?\s*$'
    return bool(re.match(pattern, text))


def parse_coordinate(text):
    """
    解析坐标字符串，返回归一化的 'lng,lat' 字符串。
    会校验坐标是否在合理范围内。
    """
    parts = [p.strip() for p in text.split(',')]
    lng, lat = float(parts[0]), float(parts[1])
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        raise ValueError(f"坐标超出合理范围: lng={lng}, lat={lat}")
    return f"{lng},{lat}"


def to_coordinate(addr):
    """
    智能转换：如果已经是坐标格式则直接解析，否则调用地理编码 API。
    """
    if is_coordinate(addr):
        return parse_coordinate(addr)
    return get_geocode(addr)


def is_gps_valid(data):
    """
    判断 GPS 定位是否有效。

    条件：
    1. 经纬度不为 None
    2. 经纬度不为 (0, 0)（传感器未定位时的默认值）
    3. 卫星数 >= GPS_MIN_SATELLITES (4)
    4. PDOP < GPS_MAX_PDOP (10)
    """
    lat = data.get('latitude')
    lon = data.get('longitude')
    sat_count = data.get('SatCount', 0)
    pdop = data.get('PDOP', 99)

    if lat is None or lon is None:
        return False
    if lat == 0.0 and lon == 0.0:
        return False
    # 坐标必须在合理范围内（中国大致范围：纬度18~54N，经度73~135E）
    if not (18.0 <= lat <= 54.0 and 73.0 <= lon <= 135.0):
        return False
    if sat_count < GPS_MIN_SATELLITES:
        return False
    if pdop >= GPS_MAX_PDOP:
        return False
    return True


def print_gps_info(data):
    """
    只打印 GPS 相关字段，不打印加速度/角速度等传感器数据
    :param data: get_data() 返回的 dict
    """
    if not data:
        print("GPS: 无数据")
        return

    lat = data.get('latitude')
    lon = data.get('longitude')
    sat_count = data.get('SatCount', 0)
    pdop = data.get('PDOP', 99)
    hdop = data.get('HDOP', 0)
    height = data.get('Height', 0)
    yaw = data.get('Yaw', 0)
    speed = data.get('GroundSpeed', 0)

    d0 = data.get('D0Status', 'N/A')

    if lon is not None and lat is not None:
        print(
            f"[GPS] 经度={lon:.6f}° 纬度={lat:.6f}° | "
            f"地速={speed:.1f}km/h 航向={yaw:.1f}° 高度={height:.1f}m | "
            f"卫星数={sat_count} PDOP={pdop} HDOP={hdop} | "
            f"D0Status={d0}"
        )
    else:
        print(f"[GPS] 未定位 | 卫星数={sat_count} PDOP={pdop} HDOP={hdop} | D0Status={d0}")


def get_simulated_speed():
    """
    随机从预设列表中返回一个模拟车速。
    用闭包保持上次的选择和时间戳，避免每次调用都随机跳变。
    """
    if not hasattr(get_simulated_speed, '_last_speed'):
        get_simulated_speed._last_speed = random.choice(SIMULATED_SPEEDS)
        get_simulated_speed._last_time = time.time()

    now = time.time()
    if now - get_simulated_speed._last_time >= SPEED_CHANGE_INTERVAL:
        # 避免连续两次选到同一个值，让变化更自然
        candidates = [s for s in SIMULATED_SPEEDS if s != get_simulated_speed._last_speed]
        get_simulated_speed._last_speed = random.choice(candidates) if candidates else get_simulated_speed._last_speed
        get_simulated_speed._last_time = now

    return get_simulated_speed._last_speed


def get_route_plan(origin_addr, dest_addr):
    """
    根据起点和终点地址，获取路线序列（简单版本，向后兼容）

    参数:
        origin_addr: 起点地址，如 "重庆北站北广场"
        dest_addr:   终点地址，如 "重庆西站停车场"

    返回:
        dict: 如 {"sequence1": {"route": "长安街-南池子大街", "Traffic": "畅通"}, ...}
        None: 查询失败时返回
    """
    # 1. 地理编码（支持直接传入坐标）
    origin_coord = to_coordinate(origin_addr)
    if not origin_coord:
        print("[错误] 无法获取起点坐标")
        return None

    dest_coord = to_coordinate(dest_addr)
    if not dest_coord:
        print("[错误] 无法获取终点坐标")
        return None

    # 2. 路径规划
    result = get_driving_direction(
        origin=origin_coord,
        destination=dest_coord,
        show_fields="tmcs,cost,polyline",
    )
    if not result:
        print("[错误] 路径规划失败")
        return None

    # 3. 提取路线序列
    sequences = extract_route_sequences(result)
    return sequences


# ========== 带缓存的路线加载 ==========

def init_route_with_cache(origin_addr, dest_addr):
    """
    获取路线规划，优先调用 API，失败则从缓存加载。

    参数:
        origin_addr: 起点地址
        dest_addr:   终点地址

    返回:
        (route_steps, raw_result):
            路段列表、原始 API 结果
            如果缓存也没有，全部为 None
    """
    # 1. 地理编码
    origin_coord = to_coordinate(origin_addr)
    if not origin_coord:
        print("[错误] 无法获取起点坐标")
        return None, None

    dest_coord = to_coordinate(dest_addr)
    if not dest_coord:
        print("[错误] 无法获取终点坐标")
        return None, None

    # 2. 路径规划（带缓存回退）
    raw_result = get_driving_direction(
        origin=origin_coord,
        destination=dest_coord,
        show_fields="tmcs,cost,polyline",
    )

    if raw_result and raw_result.get("status") == "1":
        # API 成功 → 保存缓存
        print("  >> 已调用高德API")
        save_route_cache(raw_result)
        route_steps = build_route_steps(raw_result)
        # 打印完整路径信息
        parse_driving_result(raw_result)
        return route_steps, raw_result

    # API 失败 → 尝试从缓存加载
    print("[警告] API 调用失败，尝试加载缓存...")
    raw_result = load_route_cache()
    if not raw_result:
        print("[致命] 无缓存数据，无法继续运行")
        return None, None

    route_steps = build_route_steps(raw_result)
    return route_steps, raw_result


def _build_segment_states(route_steps):
    """根据 route_steps 构建初始的全路段状态列表，每个路段附带运行时字段"""
    segment_states = []
    for step in route_steps:
        seg_dist = step.get("distance", 0)
        segment_states.append({
            "sequence": step["step_index"] + 1,
            "route": step.get("road_name", "") or step.get("instruction", ""),
            "instruction": step.get("instruction", ""),
            "distance_m": seg_dist,
            "remaining_distance_m": seg_dist,  # 初始时未行驶，剩余=全长
            "traffic": step.get("traffic", ""),
            "status": "pending",
            "speed": None,
            "speed_source": None,
            "can_speed": None,
            "driving": "",
            "engy_mode": "",
            "soc": "",
            "gear": "",
            "throttle": "",
            "battery": "",
            "batt_curr": None,
            "batt_volt": None,
            "batt_temp_max": None,
            "d0_status": "N/A",
        })
    return segment_states


def init_session(origin_addr=None, dest_addr=None):
    """
    初始化会话：打开传感器、CAN，加载路线。返回 session 字典供 step()/shutdown_session() 使用。

    参数:
        origin_addr: 起点地址（None 则使用内置默认值）
        dest_addr:   终点地址（None 则使用内置默认值）

    返回:
        dict: session 状态，包含所有运行时对象；失败返回 None
    """
    # 测试模式绕开硬件与地图初始化，让 car_server 能立即发送 main 的模拟输出。
    if is_main_test_data_enabled():
        print("[MAIN TEST] 已启用测试 output_data，不连接 GPS、CAN 或高德 API")
        return {"test_data": True, "test_frame_index": 0, "finished": False}

    session = {}

    # ====== 1. 初始化 GPS 传感器（真实 / 模拟 可切换） ======
    if MOCK_GPS:
        # 模拟 GPS 模式：不打开串口，step() 中直接用固定坐标
        reader = None
        print("[信息] 模拟 GPS 已启用（固定坐标模拟位置）")
    elif USE_NETWORK_GPS:
        from network_reader import NetworkReader
        reader = NetworkReader(host=NETWORK_GPS_HOST, port=NETWORK_GPS_PORT)
        reader.open()
    else:
        port = "/dev/ttyUSB0" if platform.system().lower() == 'linux' else "COM10"
        reader = SensorReader(port=port, baud=9600)
        reader.open()
    session['reader'] = reader

    # ====== 2. 初始化 CAN 数据源（真实 CAN 盒 / Simulink 仿真 可切换） ======
    if USE_SIMULINK:
        # 仿真模式：从 Simulink 模型获取 CAN 信号，不初始化真实 CAN 设备
        from simulink_client import SimulinkClient
        sim_client = SimulinkClient(
            recv_host=SIMULINK_RECV_HOST, recv_port=SIMULINK_RECV_PORT,
            send_host=SIMULINK_SEND_HOST, send_port=SIMULINK_SEND_PORT,
        )
        try:
            sim_client.connect()
        except Exception as e:
            print(f"[致命] Simulink 仿真客户端连接失败: {e}")
            reader.close()
            return None
        session['simulink'] = sim_client
        session['can_ok'] = False
        session['can_mock'] = False
        print("[信息] Simulink 仿真数据源已启用（替代 CAN 盒）")
    elif MOCK_CAN:
        # Mock 模式：无需硬件，仅加载DBC + 模拟信号注入
        if not init_can_mock():
            print("[致命] CAN Mock 初始化失败")
            reader.close()
            return None
        session['can_ok'] = True
        session['can_mock'] = True
    else:
        # 真实 CAN 模式
        if not init_can():
            print("[致命] CAN 设备初始化失败")
            reader.close()
            return None
        session['can_ok'] = True
        session['can_mock'] = False

    # ====== 3. 启动 CAN 自发自收测试 ======
    if USE_CAN_TEST_TX and not USE_SIMULINK:
        if session.get('can_mock'):
            if not start_mock_test_tx():
                print("[警告] Mock 信号注入启动失败")
            else:
                print("[信息] CAN Mock 模拟信号注入已启动")
        else:
            if not start_test_tx():
                print("[警告] 测试报文发送启动失败，CAN 信号将为空")
            else:
                print("[信息] CAN 自发自收测试模式已启动")
    session['can_test'] = USE_CAN_TEST_TX

    # ====== 4. 起点终点配置 ======
    if origin_addr is None:
        origin_addr = "106.830767,29.716379"
    if dest_addr is None:
        dest_addr = "106.834505,29.715720"
    session['origin_addr'] = origin_addr
    session['dest_addr'] = dest_addr

    print(f"\n{'=' * 60}")
    print(f"  起点: {origin_addr}")
    print(f"  终点: {dest_addr}")
    print(f"{'=' * 60}")

    # ====== 5. 加载路线 ======
    route_steps, raw_result = init_route_with_cache(origin_addr, dest_addr)
    if not route_steps:
        print("[致命] 无法获取路线数据")
        reader.close()
        shutdown_can()
        return None
    session['route_steps'] = route_steps
    session['raw_result'] = raw_result

    # ====== 6. 初始化路线跟踪器 ======
    tracker = RouteTracker(route_steps)
    print(f"  原始总距离: {tracker.original_total_distance/1000:.1f} km")
    session['tracker'] = tracker

    # ====== 7. 初始化运行时状态 ======
    session['last_api_refresh'] = time.time()
    session['finished'] = False

    # ====== 8. 初始化全路段状态列表 ======
    session['segment_states'] = _build_segment_states(route_steps)

    # ====== 9. 填充 HTTP 路线数据 + 启动后台 HTTP 服务 ======
    _API_ROUTE_DATA.update({
        'origin': {'lng': None, 'lat': None, 'name': origin_addr},
        'destination': {'lng': None, 'lat': None, 'name': dest_addr},
        'total_distance': tracker.original_total_distance,
        'steps': route_steps,
    })
    _API_STATUS_DATA.update({
        'total_steps': len(route_steps),
        'finished': False,
    })
    if ENABLE_HTTP_DASHBOARD:
        start_http_background()

    print("\n会话初始化完成.\n")
    return session


def step(session):
    """
    执行一次数据处理循环，返回 output_data 字典（供 AI 上位机逐帧调用）。

    参数:
        session: init_session() 返回的会话状态字典

    返回:
        dict 或 None: output_data (含 CAN/导航/路况等)；无数据/已结束时返回 None
    """
    if session.get("test_data"):
        frame_index = session["test_frame_index"]
        session["test_frame_index"] = frame_index + 1
        output_data = build_test_output_data(frame_index)
        print(f"[MAIN TEST] 已生成测试帧 {frame_index + 1}: current_segment="
              f"{output_data['route_summary']['current_segment']}")
        return output_data

    reader = session.get('reader')
    tracker = session['tracker']
    dest_addr = session['dest_addr']

    # ====== 读取传感器数据（GPS 来源：真实传感器 / 模拟坐标） ======
    if MOCK_GPS:
        # 模拟 GPS 模式：直接构造固定坐标数据，不读取真实传感器
        import time as _time
        data = {
            'latitude': MOCK_GPS_LAT,
            'longitude': MOCK_GPS_LON,
            'GroundSpeed': MOCK_GPS_SPEED,
            'SatCount': 10,
            'PDOP': 2.0,
            'HDOP': 1.0,
            'Height': 250,
            'Yaw': 90,
            'D0Status': '0',
            'Chiptime': str(_time.time()),
        }
    else:
        data = reader.get_data()
        if not data:
            # 无真实 GPS 时用模拟数据兜底
            import time as _time
            data = {
                'latitude': 29.7165,
                'longitude': 106.8310,
                'GroundSpeed': 45.0,
                'SatCount': 10,
                'PDOP': 2.0,
                'HDOP': 1.0,
                'Height': 250,
                'Yaw': 90,
                'D0Status': '0',
                'Chiptime': str(_time.time()),
            }

    # ====== 到达终点检查 ======
    if tracker.is_finished():
        session['finished'] = True
        print(f"\n{'='*60}")
        print("  已到达终点！行程结束。")
        print(f"{'='*60}")
        return None

    # ====== 打印 GPS 信息 ======
    print_gps_info(data)

    now = time.time()
    lat = data.get('latitude')
    lon = data.get('longitude')

    # ---------- GPS 有效性判断 ----------
    gps_valid = is_gps_valid(data)


    # ---------- CAN 信号读取（按开关选择数据源：Simulink 仿真 / 真实 CAN 盒） ----------
    if USE_SIMULINK:
        sim_client = session.get('simulink')
        can_signals = sim_client.get_signals() if sim_client else {}
    else:
        can_signals = get_all_signals()
    can_speed = can_signals.get("VITESSE_VEHICULE_ROUES")  # CAN 车速 (km/h)
    can_batt_curr = can_signals.get("HV_BATT_REAL_CURR_HD")  # 电池电流 (A)
    can_batt_volt = can_signals.get("HV_BATT_REAL_VOLT_HD")  # 电池电压 (V)

    # # ====== CAN 调试打印 ======
    source_label = "Simulink" if USE_SIMULINK else "CAN"
    print(f"[{source_label}-DEBUG] 收到信号数: {len(can_signals)}, 所有信号: {can_signals}")
    # print(f"[{source_label}-DEBUG] 车速(VITESSE_VEHICULE_ROUES) = {can_speed}, 看门狗故障 = {can_signals.get('_watchdog_fault')}")
    # # ==========================


    # 速度优先级：CAN 车速 > GPS 地速 > 模拟车速
    if USE_SIMULATED_SPEED:
        speed = get_simulated_speed()
        speed_source = "simulated"
    elif can_speed is not None and can_speed > 0:
        speed = can_speed
        speed_source = "can"
    elif gps_valid:
        speed = data.get('GroundSpeed', 0)
        speed_source = "gps"
    else:
        # 速度兜底策略
        # speed = get_simulated_speed()
        # speed_source = "simulated_fallback"
        speed = 0
        speed_source = "none"

    # ---------- 路线跟踪 ----------
    if gps_valid:
        status = tracker.update(lat, lon, speed, now)
        if status and status.get("gps_recovered"):
            print(f"\n{'='*50}")
            print(f"  [GPS恢复] 信号已恢复！当前位置: ({lat:.6f}, {lon:.6f})")
            print(f"            已保存的失效前最后位置: ({tracker.last_valid_lat:.6f}, {tracker.last_valid_lon:.6f})")
            print(f"{'='*50}")
            session['last_api_refresh'] = 0
    else:
        status = tracker.dead_reckon(speed, now)
        if status and status.get("gps_lost"):
            save_lat = status.get("last_valid_lat")
            save_lon = status.get("last_valid_lon")
            coord_str = f"({save_lat:.6f}, {save_lon:.6f})" if save_lat is not None else "暂无"
            print(f"\n{'='*50}")
            print(f"  [GPS丢失] 信号已丢失！已保存最后位置: {coord_str}")
            print(f"            将停止 API 请求，进入航位推算模式...")
            print(f"{'='*50}")

    # ---------- API 路况刷新 ----------
    if now - session['last_api_refresh'] > API_REFRESH_INTERVAL:
        if gps_valid and lat is not None and lon is not None:
            print(f"\n[刷新] 正在更新路况...")
            print(f"  >> 已调用高德API")
            origin_coord = f"{lon},{lat}"
            dest_coord = to_coordinate(dest_addr)
            if dest_coord:
                new_result = get_driving_direction(
                    origin=origin_coord,
                    destination=dest_coord,
                    show_fields="tmcs,cost,polyline",
                )
                if new_result and new_result.get("status") == "1":
                    save_route_cache(new_result)
                    route_steps = build_route_steps(new_result)
                    if route_steps:
                        tracker.reload_steps(route_steps, lat, lon)
                        session['route_steps'] = route_steps
                        session['segment_states'] = _build_segment_states(route_steps)
                        print("[刷新] 路况已更新")
                    else:
                        print("[刷新] build_route_steps 返回空")
                else:
                    print("[刷新] API 返回失败，跳过本次刷新")
        session['last_api_refresh'] = now

    # ---------- 构建全路段 output_data ----------
    step_info = tracker.get_current_step()
    current_idx = tracker.current_step_idx
    progress = tracker.get_progress_pct()
    remaining_km = max(0, (tracker.original_total_distance - tracker.cumulative_distance) / 1000)

    # CAN 信号格式化（can_signals 已在上方获取）
    can_driving  = translate_dyn_mode(can_signals.get("STDE_DRV_DYN_MODE_STATE"))
    can_engy     = translate_engy_mode(can_signals.get("STDE_DRV_ENGY_MODE_STATE"))
    can_soc      = f"{can_signals.get('HV_BATT_SOC', 0):.1f}%" if can_signals.get('HV_BATT_SOC') is not None else ""
    can_gear     = translate_gear(can_signals.get("POS_MONOSTABLE_LEVER"))
    throttle_val = can_signals.get('EFCMNT_PDLE_ACCEL_228') or can_signals.get('EFCMNT_PDLE_ACCEL_278')
    can_throttle = f"{throttle_val:.1f}%" if throttle_val is not None else ""
    can_battery  = f"{can_signals.get('HV_BATT_TEMP_AVG', 0):.0f}°C" if can_signals.get('HV_BATT_TEMP_AVG') is not None else ""
    can_batt_temp_max = can_signals.get("HV_BATT_TEMP_MAX")  # 电池最高温度 (degC)

    # 当前路段运行时数据快照
    runtime_snapshot = {
        "speed": round(speed, 1),
        "speed_source": speed_source,
        "can_speed": round(can_speed, 1) if can_speed is not None else None,
        "driving": can_driving,
        "engy_mode": can_engy,
        "soc": can_soc,
        "gear": can_gear,
        "throttle": can_throttle,
        "battery": can_battery,
        "batt_curr": round(can_batt_curr, 1) if can_batt_curr is not None else None,
        "batt_volt": round(can_batt_volt, 1) if can_batt_volt is not None else None,
        "batt_temp_max": round(can_batt_temp_max, 1) if can_batt_temp_max is not None else None,  # 电池最高温度 (数值)
        "d0_status": data.get('D0Status', 'N/A'),
    }

    # ---------- 更新所有路段状态 ----------
    segment_states = session['segment_states']
    n_segments = len(segment_states)
    for i, seg in enumerate(segment_states):
        seg_distance = seg.get("distance_m", 0)
        if i < current_idx:
            # 已走过的路段：标记为 completed，剩余距离为 0
            seg["status"] = "completed"
            seg["remaining_distance_m"] = 0
        elif i == current_idx and i < n_segments:
            # 当前路段：更新运行时数据，计算剩余距离
            seg["status"] = "current"
            seg.update(runtime_snapshot)
            seg["remaining_distance_m"] = max(0, seg_distance - tracker.distance_in_step)
            if step_info:
                seg["traffic"] = step_info.get('traffic', seg["traffic"])
        else:
            # 未到达的路段：剩余距离 = 该路段全长
            seg["status"] = "pending"
            seg["remaining_distance_m"] = seg_distance

    # ---------- 构建最终返回的 JSON ----------
    output_data = {
        "route_summary": {
            "total_distance_km": round(tracker.original_total_distance / 1000, 2),
            "remaining_km": round(remaining_km, 2),
            "progress_pct": round(progress, 1),
            "accumulated_dist_m": round(tracker.accumulated_dist, 1),
            "current_segment": current_idx + 1 if 0 <= current_idx < n_segments else None,
        },
        "segments": segment_states,
    }

    # 两种模式都把原始信号挂上，供车端打印/调试（不影响 AI 主数据 simulink/segments）
    # output_data["can_signals"] = can_signals

    # 仿真模式下补充 simulink 字段，让 receive_demo.py 的 AI 决策脚本能直接取精确数据
    if USE_SIMULINK:
        output_data["simulink"] = {
            "EFCMNT_PDLE_ACCEL":       can_signals.get("EFCMNT_PDLE_ACCEL_228"),
            "HV_BATT_TEMP_AVG":        can_signals.get("HV_BATT_TEMP_AVG"),
            "VITESSE_VEHICULE_ROUES":  can_signals.get("VITESSE_VEHICULE_ROUES"),
            "POS_MONOSTABLE_LEVER":    can_signals.get("POS_MONOSTABLE_LEVER"),
            "HV_BATT_SOC":             can_signals.get("HV_BATT_SOC"),
            "HV_BATT_REAL_CURR_HD":    can_signals.get("HV_BATT_REAL_CURR_HD"),
            "HV_BATT_REAL_VOLT_HD":    can_signals.get("HV_BATT_REAL_VOLT_HD"),
            "STDE_DRV_DYN_MODE_STATE": can_signals.get("STDE_DRV_DYN_MODE_STATE"),
            "STDE_DRV_ENGY_MODE_STATE": can_signals.get("STDE_DRV_ENGY_MODE_STATE"),
        }
    print(output_data)

    # ---------- 更新 HTTP 共享状态 ----------
    _update_http_status(data, gps_valid, speed, step_info, progress, tracker, lat, lon)
    # ---------- 追加历史数据（供 GPS 仪表盘使用） ----------
    _append_history(gps_valid, speed, step_info, progress, tracker, lat, lon)

    # ---------- 打印输出 ----------
    if step_info:
        route_display = step_info['road_name'] if step_info['road_name'] else step_info['instruction']
        print(f"\n--- Segment {step_info['step_index'] + 1}/{n_segments} ---")
        # print(f"  Route: {route_display}")
        # print(f"  Traffic: {step_info['traffic']}")
        # print(f"  Speed: {speed:.1f} km/h (来源: {speed_source})")
        # print(f"  Progress: {progress:.1f}%")
        # print(f"  推算距离: {tracker.accumulated_dist:.0f}m")
        # print(f"  Driving: {can_driving}")
        # print(f"  EngyMode: {can_engy}")
        # print(f"  SOC: {can_soc}")
        # print(f"  Gear: {can_gear}")
        # print(f"  Throttle: {can_throttle}")
        # print(f"  Battery: {can_battery}")
        # print(f"  惯导收敛: {data.get('D0Status', 'N/A')}")
    else:
        if tracker.is_finished():
            print(f"\n--- 已到达终点 ---")
        else:
            print(f"\n--- 等待定位... ---")

    return output_data


# ============ HTTP API 处理器 ============
class _RouteAPIHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器：返回 HTML 页面 + JSON API"""

    def do_GET(self):
        try:
            if self.path == '/dashboard':
                self._serve_dashboard()
            elif self.path == '/api/route':
                self._serve_json(_API_ROUTE_DATA)
            elif self.path == '/api/status':
                self._serve_json(_API_STATUS_DATA)
            elif self.path == '/api/history':
                self._serve_json(_HISTORY_DATA)
            else:
                self.send_error(404)
        except Exception:
            self.send_error(500)

    def _serve_dashboard(self):
        try:
            with open(_DASHBOARD_PATH, 'r', encoding='utf-8') as f:
                html = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(html.encode('utf-8'))))
            self.end_headers()
            self.wfile.write(html.encode('utf-8'))
        except FileNotFoundError:
            self.send_error(404, 'gps_dashboard.html not found')

    def _serve_json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # 抑制控制台日志


def _start_http_server():
    """在后台线程启动 HTTP 服务器"""
    server = HTTPServer(('0.0.0.0', 8081), _RouteAPIHandler)
    print(f"[HTTP] 服务器已启动 → http://localhost:8081")
    print(f"[HTTP] GPS仪表盘 → http://localhost:8081/dashboard")
    try:
        server.serve_forever()
    except Exception:
        pass


def start_http_background():
    """启动后台 HTTP 线程（守护线程，主程序退出时自动结束）"""
    t = threading.Thread(target=_start_http_server, daemon=True)
    t.start()
    return t


def _update_http_status(data, gps_valid, speed, step_info, progress, tracker, lat, lon):
    """将主循环状态同步到 HTTP 共享字典"""
    remaining_km = max(0, (tracker.original_total_distance - tracker.cumulative_distance) / 1000)
    _API_STATUS_DATA.update({
        'lat': lat,
        'lng': lon,
        'speed': speed,
        'progress_pct': round(progress, 1),
        'remaining_km': round(remaining_km, 2),
        'current_step': step_info['step_index'] if step_info else 0,
        'road_name': step_info['road_name'] if step_info else '',
        'traffic': step_info['traffic'] if step_info else '',
        'gps_valid': gps_valid,
        'gps_lost': tracker.gps_lost,
        'api_offline': tracker.gps_lost,  # GPS丢失时进入离线推算，标记API离线
        'finished': tracker.is_finished(),
        'accumulated_dist': round(tracker.accumulated_dist, 1),
        'sat_count': data.get('SatCount', 0),
        'hdop': data.get('HDOP', 0.0),
    })


def _append_history(gps_valid, speed, step_info, progress, tracker, lat, lon, is_api_refresh=False):
    """将当前状态追加到历史数据缓冲区（用于 GPS 仪表盘曲线图）"""
    import datetime
    remaining_km = max(0, (tracker.original_total_distance - tracker.cumulative_distance) / 1000)
    hist = _HISTORY_DATA

    hist['timestamps'].append(datetime.datetime.now().strftime('%H:%M:%S'))
    hist['progress_pct'].append(round(progress, 1))
    hist['remaining_km'].append(round(remaining_km, 2))
    hist['speed'].append(round(speed, 1))
    hist['lat'].append(lat if lat else None)
    hist['lng'].append(lon if lon else None)
    hist['gps_valid'].append(gps_valid)
    hist['api_refresh'].append(is_api_refresh)
    hist['current_step'].append(step_info['step_index'] if step_info else 0)
    hist['traffic'].append(step_info['traffic'] if step_info else '')

    # 裁剪：只保留最近 _MAX_HISTORY 条
    if len(hist['timestamps']) > _MAX_HISTORY:
        for key in hist:
            hist[key] = hist[key][-_MAX_HISTORY:]
# ======================================


def shutdown_session(session):
    """关闭会话，释放所有资源（CAN、传感器等）。"""
    if session.get("test_data"):
        print("[MAIN TEST] 测试会话已关闭")
        return
    # Simulink 仿真模式：关闭仿真客户端连接
    if session.get('simulink'):
        session['simulink'].close()
        reader = session.get('reader')
        if reader:
            reader.close()
        print("[Simulink] 仿真会话已关闭")
        return
    if session.get('can_test'):
        if session.get('can_mock'):
            stop_mock_test_tx()
        else:
            stop_test_tx()
    if session.get('can_mock'):
        shutdown_can_mock()
    else:
        shutdown_can()
    reader = session.get('reader')
    if reader:
        reader.close()
    print("设备已关闭")


def main():
    """
    独立运行的主循环
    AI 上位机调用时请直接使用 init_session() → step() → shutdown_session() 三元组。
    """
    session = init_session()
    if not session:
        return

    print("开始行驶监控，按 Ctrl+C 停止...\n")

    try:
        while True:
            output = step(session)
            if output is None and session.get('finished'):
                break
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n用户中断")
    finally:
        shutdown_session(session)


if __name__ == '__main__':
    main()


'''
上位机调用示例：
from main import init_session, step, shutdown_session

session = init_session("106.830767,29.716379", "106.834505,29.715720")
while True:
    data = step(session)
    if data is None:
        break
    # 处理 data（发送、存储、决策...）
shutdown_session(session)

起点: 106.830767,29.716379
终点: 106.834505,29.715720
'''
