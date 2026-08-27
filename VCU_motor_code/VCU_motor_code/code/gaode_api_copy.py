"""
高德地图 驾车路径规划 API 封装
API 文档: https://lbs.amap.com/api/webservice/guide/api/direction
"""
import requests
import json


# ============ 配置 ============
# 高德地图 Web API Key
AMAP_KEY = "54a064537d6285ea495ebcc79c711126"

# API 基础地址
AMAP_DRIVING_URL = "https://restapi.amap.com/v5/direction/driving"
AMAP_GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
# ============================


def get_geocode(address, city=None):
    """
    调用高德地图 V3 地理编码 API，将地址转换为经纬度坐标

    参数:
        address: 结构化地址信息（必填）
        city:    指定查询的城市（可选），如 "北京"、"深圳"

    返回:
        str:  "lng,lat" 格式的坐标字符串，如 "116.397428,39.90923"
        None: 查询失败时返回
    """
    request_params = {
        "key": AMAP_KEY,
        "address": address,
    }
    if city:
        request_params["city"] = city

    print(f"[地理编码] 查询地址: {address}")

    try:
        response = requests.get(
            url=AMAP_GEOCODE_URL,
            params=request_params,
            timeout=10
        )
        response.raise_for_status()
        result = response.json()
    except requests.exceptions.Timeout:
        print("[错误] 地理编码请求超时")
        return None
    except requests.exceptions.ConnectionError:
        print("[错误] 地理编码网络连接失败")
        return None
    except Exception as e:
        print(f"[错误] 地理编码请求异常: {e}")
        return None

    if result.get("status") != "1":
        print(f"[地理编码失败] {result.get('info', '未知错误')}")
        return None

    geocodes = result.get("geocodes", [])
    if not geocodes:
        print(f"[地理编码] 未找到地址 '{address}' 对应的坐标")
        return None

    location = geocodes[0].get("location")
    if not location:
        print(f"[地理编码] 返回结果中没有 location 字段")
        return None

    print(f"[地理编码] {address} -> {location}")
    return location


def get_driving_direction(**params):
    """
    请求高德地图 V5 驾车路径规划接口

    参数:
        origin:        起点坐标 "lng,lat"（必填）
        destination:   终点坐标 "lng,lat"（必填）
        key:           高德 API Key（默认使用全局配置）
        strategy:      路径规划策略，0-32（可选）
                       0: 速度优先（默认）
                       1: 费用优先
                       2: 距离优先
                       10: 躲避拥堵 & 速度优先
                       32: 大路优先
        waypoints:     途经点坐标，多个用"|"分隔（可选）
        show_fields:   返回字段控制，如 "cost,polyline"（可选）
        **params:      其他自定义参数，会自动添加到请求中

    返回:
        dict: API 响应的 JSON 数据
        None: 请求失败时返回
    """
    # 1. 构建参数字典，自动加入 key
    request_params = {
        "key": AMAP_KEY,
    }

    # 2. 合并用户传入的所有参数
    request_params.update(params)

    # 3. 参数校验
    if "origin" not in request_params:
        print("[错误] 缺少必填参数: origin（起点坐标）")
        return None
    if "destination" not in request_params:
        print("[错误] 缺少必填参数: destination（终点坐标）")
        return None

    print(f"[请求] GET {AMAP_DRIVING_URL}")
    print(f"[参数] {json.dumps(request_params, ensure_ascii=False, indent=2)}")
    print("-" * 50)

    # 4. 发送 GET 请求
    try:
        response = requests.get(
            url=AMAP_DRIVING_URL,
            params=request_params,
            timeout=10  # 10秒超时
        )
        response.raise_for_status()  # 检查 HTTP 状态码
    except requests.exceptions.Timeout:
        print("[错误] 请求超时")
        return None
    except requests.exceptions.ConnectionError:
        print("[错误] 网络连接失败")
        return None
    except requests.exceptions.HTTPError as e:
        print(f"[错误] HTTP 错误: {e}")
        return None
    except Exception as e:
        print(f"[错误] 请求异常: {e}")
        return None

    # 5. 解析响应
    try:
        result = response.json()
    except json.JSONDecodeError:
        print(f"[错误] 响应不是有效的 JSON: {response.text[:200]}")
        return None

    # 6. 检查业务状态
    if result.get("status") == "0":
        print(f"[API 错误] {result.get('errcode')}: {result.get('errdetail')}")
        return result  # 仍然返回，方便调用方自行处理

    print(f"[成功] 状态: {result.get('status')}, 消息: {result.get('errdetail', 'ok')}")
    return result


def parse_driving_result(result):
    """
    解析并美化输出路径规划结果

    参数:
        result: get_driving_direction() 的返回值
    """
    if not result or result.get("status") != "1":
        print("没有可用的路径规划结果")
        return

    route = result.get("route", {})
    print("=" * 60)
    print(f"起点: {route.get('origin')}")
    print(f"终点: {route.get('destination')}")

    paths = route.get("paths", [])
    if not paths:
        print("未找到可用路径")
        return

    # 默认使用第一条推荐路线
    path = paths[0]
    print(f"\n--- 推荐路线 ---")
    distance_km = int(path.get('distance', 0)) / 1000
    print(f"  距离: {distance_km:.2f} 公里")

    # 费用信息（如果请求时指定了 show_fields=cost）
    cost = path.get("cost")
    if cost:
        print(f"  高速费: {cost.get('tolls', 0)} 元")

    # 交通灯数量
    traffic_lights = path.get("traffic_lights", 0)
    if traffic_lights:
        print(f"  红绿灯: {traffic_lights} 个")

    # 步数 & 路况汇总
    steps = path.get("steps", [])
    if steps:
        # 路况状态
        traffic_order = ["未知", "畅通", "缓行", "拥堵", "严重拥堵"]
        traffic_count = {}
        for step in steps:
            tmcs = step.get("tmcs", [])
            for tmc in tmcs:
                status = tmc.get("tmc_status", "未知")
                traffic_count[status] = traffic_count.get(status, 0) + 1

        print(f"  导航步骤: {len(steps)} 步")

        # 依次打印所有步骤的详细信息（含路况）
        print(f"\n  详细指引:")
        for j, step in enumerate(steps, 1):

            # print(step)
            # exit()
            instruction = step.get("instruction", "")
            road = step.get("road", "")
            distance_m = step.get("distance", 0)
            step_km = int(distance_m) / 1000 if distance_m else 0

            tmcs = step.get("tmcs", [])
            if tmcs:
                tname = tmcs[0].get("tmc_status", "未知")
            else:
                tname = "未知"

            if step_km > 0:
                print(f"    {j}. {instruction} [{road}] {step_km:.2f}km 路况:{tname}")
            else:
                print(f"    {j}. {instruction} [{road}] 路况:{tname}")

    print("=" * 60)


# ============ 交互式主程序 ============
if __name__ == "__main__":
    print("=" * 50)
    print("  高德地图 驾车路径规划")
    print("=" * 50)

    # 1. 输入起点地址
    origin_addr = input("\n请输入起点地址: ").strip()
    if not origin_addr:
        print("起点地址不能为空")
        exit(1)

    # 2. 输入终点地址
    dest_addr = input("请输入终点地址: ").strip()
    if not dest_addr:
        print("终点地址不能为空")
        exit(1)

    # 3. 查询起点经纬度
    print()
    origin_coord = get_geocode(origin_addr)
    if not origin_coord:
        print("无法获取起点坐标，程序退出")
        exit(1)

    # 4. 查询终点经纬度
    dest_coord = get_geocode(dest_addr)
    if not dest_coord:
        print("无法获取终点坐标，程序退出")
        exit(1)

    # 5. 调用路径规划
    print()
    result = get_driving_direction(
        origin=origin_coord,
        destination=dest_coord,
        show_fields="tmcs,cost,polyline",
    )

    if result:
        parse_driving_result(result)
