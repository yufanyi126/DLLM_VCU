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
import platform
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# ========== 路径配置：将 chs 目录加入搜索路径 ==========
_CHS_DIR = os.path.join(
    os.path.dirname(__file__), '..',
    'WitStandardModbus_WT901C485-main', 'Python', 'Python-SDK-WT901C485', 'chs'
)
sys.path.insert(0, _CHS_DIR)

from sensor_reader import SensorReader
from gaode_api import (
    get_geocode, get_driving_direction,
    extract_route_sequences, build_route_steps,
    save_route_cache, load_route_cache,
    parse_driving_result,
)
from route_tracker import RouteTracker


# ============ 配置 ============
API_REFRESH_INTERVAL = 60      # API 路况刷新间隔（秒）
GPS_MIN_SATELLITES = 4         # GPS 有效所需最少卫星数
GPS_MAX_PDOP = 10.0             # GPS 有效所需最大 PDOP，PDOP 越小越精确

# ---------- 模拟车速配置 ----------
USE_SIMULATED_SPEED = True    # True=强制模拟车速, False=GPS有效时使用真实地速,无效时模拟
SIMULATED_SPEEDS = [           # 模拟车速列表 (km/h)，会随机选一个
    50, 55, 60, 65, 70, 80, 90,
]
SPEED_CHANGE_INTERVAL = 5.0    # 每隔多少秒更换一次随机速度（避免太抖动）
# ============================

# ============ HTTP 共享数据 ============
_API_ROUTE_DATA = {}       # 路线数据（初始化后不变）
_API_STATUS_DATA = {        # 实时状态（主循环每次更新）
    'lat': None, 'lng': None, 'speed': 0,
    'progress_pct': 0.0, 'remaining_km': 0.0,
    'current_step': 0, 'total_steps': 0,
    'road_name': '', 'traffic': '',
    'gps_valid': False, 'finished': False,
    'gps_lost': False, 'accumulated_dist': 0,
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
# =======================================


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

    if lon is not None and lat is not None:
        print(
            f"[GPS] 经度={lon:.6f}° 纬度={lat:.6f}° | "
            f"地速={speed:.1f}km/h 航向={yaw:.1f}° 高度={height:.1f}m | "
            f"卫星数={sat_count} PDOP={pdop} HDOP={hdop}"
        )
    else:
        print(f"[GPS] 未定位 | 卫星数={sat_count} PDOP={pdop} HDOP={hdop}")


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


# ========== HTTP 服务器 ==========

_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'driving_route.html')
_DASHBOARD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'gps_dashboard.html')


class _RouteAPIHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器：返回 HTML 页面 + JSON API"""

    def do_GET(self):
        try:
            if self.path == '/' or self.path == '/index.html':
                self._serve_html()
            elif self.path == '/dashboard':
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

    def _serve_html(self):
        try:
            with open(_HTML_PATH, 'r', encoding='utf-8') as f:
                html = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(html.encode('utf-8'))))
            self.end_headers()
            self.wfile.write(html.encode('utf-8'))
        except FileNotFoundError:
            self.send_error(404, 'driving_route.html not found')

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
        body = json.dumps(data, ensure_ascii=False)
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(body.encode('utf-8'))

    def log_message(self, format, *args):
        pass  # 抑制控制台日志


def _start_http_server():
    """在后台线程启动 HTTP 服务器"""
    server = HTTPServer(('0.0.0.0', 8081), _RouteAPIHandler)
    print(f"[HTTP] 服务器已启动 → http://localhost:8081")
    try:
        server.serve_forever()
    except Exception:
        pass


def start_http_background():
    """启动后台 HTTP 线程（守护线程，主程序退出时自动结束）"""
    t = threading.Thread(target=_start_http_server, daemon=True)
    t.start()
    return t


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
    # 1. 地理编码
    origin_coord = get_geocode(origin_addr)
    if not origin_coord:
        print("[错误] 无法获取起点坐标")
        return None

    dest_coord = get_geocode(dest_addr)
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


# ========== 核心：带缓存的路线加载 ==========

def init_route_with_cache(origin_addr, dest_addr):
    """
    获取路线规划，优先调用 API，失败则从缓存加载。

    参数:
        origin_addr: 起点地址
        dest_addr:   终点地址

    返回:
        (route_steps, raw_result, origin_coord, dest_coord):
            路段列表、原始 API 结果、起点坐标、终点坐标
            如果缓存也没有，全部为 None
    """
    # 1. 地理编码
    origin_coord = get_geocode(origin_addr)
    if not origin_coord:
        print("[错误] 无法获取起点坐标")
        return None, None, None, None

    dest_coord = get_geocode(dest_addr)
    if not dest_coord:
        print("[错误] 无法获取终点坐标")
        return None, None, None, None

    # 2. 路径规划（带缓存回退）
    raw_result = get_driving_direction(
        origin=origin_coord,
        destination=dest_coord,
        show_fields="tmcs,cost,polyline",
    )

    if raw_result and raw_result.get("status") == "1":
        # API 成功 → 保存缓存
        save_route_cache(raw_result)
        route_steps = build_route_steps(raw_result)
        # 打印完整路径信息
        parse_driving_result(raw_result)
        return route_steps, raw_result, origin_coord, dest_coord

    # API 失败 → 尝试从缓存加载
    print("[警告] API 调用失败，尝试加载缓存...")
    raw_result = load_route_cache()
    if not raw_result:
        print("[致命] 无缓存数据，无法继续运行")
        return None, None, None, None

    route_steps = build_route_steps(raw_result)
    return route_steps, raw_result, origin_coord, dest_coord


def _update_http_status(data, gps_valid, speed, step, progress, tracker, lat, lon):
    """将主循环状态同步到 HTTP 共享字典"""
    remaining_km = max(0, (tracker.original_total_distance - tracker.cumulative_distance) / 1000)
    _API_STATUS_DATA.update({
        'lat': lat,
        'lng': lon,
        'speed': speed,
        'progress_pct': round(progress, 1),
        'remaining_km': round(remaining_km, 2),
        'current_step': step['step_index'] if step else 0,
        'road_name': step['road_name'] if step else '',
        'traffic': step['traffic'] if step else '',
        'gps_valid': gps_valid,
        'gps_lost': tracker.gps_lost,
        'finished': tracker.is_finished(),
        'accumulated_dist': round(tracker.accumulated_dist, 1),
        'sat_count': data.get('SatCount', 0),
        'hdop': data.get('HDOP', 0.0),
    })


def _append_history(gps_valid, speed, step, progress, tracker, lat, lon, is_api_refresh=False):
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
    hist['current_step'].append(step['step_index'] if step else 0)
    hist['traffic'].append(step['traffic'] if step else '')

    # 裁剪：只保留最近 _MAX_HISTORY 条
    if len(hist['timestamps']) > _MAX_HISTORY:
        for key in hist:
            hist[key] = hist[key][-_MAX_HISTORY:]


def main():
    """
    主循环：
    1. 初始化传感器
    2. 加载路线规划（API + 缓存回退）
    3. 循环读取 GPS 数据，有效性检查 + 航位推算
    4. 定期刷新路况 API
    """
    # ====== 1. 初始化传感器 ======
    port = "/dev/ttyUSB0" if platform.system().lower() == 'linux' else "COM5"
    reader = SensorReader(port=port, baud=9600)
    reader.open()

    # ====== 2. 起点终点配置 ======
    ORIGIN_ADDR = "哈尔滨工业大学重庆研究院"
    DEST_ADDR = "西北工业大学重庆科创中心西门"

    print(f"\n{'=' * 60}")
    print(f"  起点: {ORIGIN_ADDR}")
    print(f"  终点: {DEST_ADDR}")
    print(f"{'=' * 60}")

    # ====== 3. 加载路线 ======
    route_steps, raw_result, origin_coord, dest_coord = init_route_with_cache(ORIGIN_ADDR, DEST_ADDR)
    if not route_steps:
        print("[致命] 无法获取路线数据，程序退出")
        reader.close()
        return

    # ====== 4. 初始化路线跟踪器 ======
    tracker = RouteTracker(route_steps)
    print(f"  原始总距离: {tracker.original_total_distance/1000:.1f} km")

    # ====== 4.5. 填充 HTTP 路线数据 + 启动服务器 ======
    origin_lng, origin_lat = (float(v) for v in origin_coord.split(','))
    dest_lng, dest_lat = (float(v) for v in dest_coord.split(','))

    _API_ROUTE_DATA.update({
        'origin': {'lng': origin_lng, 'lat': origin_lat, 'name': ORIGIN_ADDR},
        'destination': {'lng': dest_lng, 'lat': dest_lat, 'name': DEST_ADDR},
        'total_distance': tracker.original_total_distance,
        'steps': route_steps,
    })
    _API_STATUS_DATA.update({
        'total_steps': len(route_steps),
        'finished': False,
    })
    start_http_background()

    # ====== 5. 主循环 ======
    last_api_refresh = time.time()
    print("\n开始行驶监控，按 Ctrl+C 停止...\n")

    try:
        while True:
            data = reader.get_data()
            if not data:
                time.sleep(0.1)
                continue

            # ====== 到达终点检查：提前退出循环，停止一切处理 ======
            if tracker.is_finished():
                print(f"\n{'='*60}")
                print("  已到达终点！行程结束。")
                print(f"{'='*60}")
                break

            # 每次读取后打印 GPS 原始信息
            print_gps_info(data)

            now = time.time()
            lat = data.get('latitude')
            lon = data.get('longitude')
            # ---------- GPS 有效性判断 ----------
            gps_valid = is_gps_valid(data)

            # 速度：GPS有效时使用真实地速，GPS无效时使用模拟车速
            if USE_SIMULATED_SPEED:
                speed = get_simulated_speed()       # 强制模拟车速
            elif gps_valid:
                speed = data.get('GroundSpeed', 0)  # GPS 地速 (km/h)
            else:
                speed = get_simulated_speed()       # GPS无效时模拟车速

            if gps_valid:
                tracker.update(lat, lon, speed, now)
            else:
                tracker.dead_reckon(speed, now)

            # ---------- API 路况刷新 ----------
            api_refresh_this_cycle = False
            if now - last_api_refresh > API_REFRESH_INTERVAL:
                # GPS 无效时不执行刷新（没有可靠起点坐标）
                if gps_valid and lat is not None and lon is not None:
                    print(f"\n[刷新] 正在更新路况...")
                    origin_coord = f"{lon},{lat}"
                    dest_coord = get_geocode(DEST_ADDR)
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
                                # 同步 HTTP 路线数据
                                _API_ROUTE_DATA['steps'] = route_steps
                                _API_ROUTE_DATA['total_distance'] = tracker.original_total_distance
                                _API_STATUS_DATA['total_steps'] = len(route_steps)
                                api_refresh_this_cycle = True
                                print("[刷新] 路况已更新")
                            else:
                                print("[刷新] build_route_steps 返回空")
                        else:
                            print("[刷新] API 返回失败，跳过本次刷新")
                last_api_refresh = now

            # ---------- 打印当前状态 ----------
            step = tracker.get_current_step()
            progress = tracker.get_progress_pct()
            gps_status = "GPS有效" if gps_valid else "GPS失效，系统推算中"

            if step:
                remaining_km = max(0, (tracker.original_total_distance - tracker.cumulative_distance) / 1000)
                print(f"[{gps_status}] 路段{step['step_index']+1}/{len(tracker.steps)}: "
                      f"{step['road_name']} | 路况:{step['traffic']} | "
                      f"进度:{progress:.1f}% | 剩余:{remaining_km:.1f}km | 车速:{speed:.1f}km/h | "
                      f"累计推算:{tracker.accumulated_dist:.0f}m")
            else:
                if tracker.is_finished():
                    print(f"[{gps_status}] 已到达终点！ 进度:{progress:.1f}%")
                else:
                    remaining_km = max(0, (tracker.original_total_distance - tracker.cumulative_distance) / 1000)
                    print(f"[{gps_status}] 等待定位... 进度:{progress:.1f}% 剩余:{remaining_km:.1f}km")

            # ---------- 更新 HTTP 共享状态 ----------
            _update_http_status(data, gps_valid, speed, step, progress,
                                tracker, lat, lon)

            # ---------- 追加历史数据（供 GPS 仪表盘使用） ----------
            _append_history(gps_valid, speed, step, progress, tracker, lat, lon,
                            is_api_refresh=api_refresh_this_cycle)

            time.sleep(2)  # 10Hz - 0.1s

    except KeyboardInterrupt:
        print("\n用户中断")
    finally:
        reader.close()
        print("设备已关闭")


if __name__ == '__main__':
    main()
