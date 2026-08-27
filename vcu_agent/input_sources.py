# -*- coding: utf-8 -*-
"""
【输入源模拟模块】
为 VCU Agent 提供四种数据源的模拟实现：
1. 高德API — 路线信息与沿途路况
2. RMS脚本 — 当前驾驶工况
3. VCU网关 — 车辆核心状态（SOC、车速、档位、油门、电池温度）
4. GPS模块 — 当前坐标

后续接入真实数据源时，只需替换对应类的内部实现，
保持接口（类名 + 方法签名）不变即可。
"""

import random
from datetime import datetime
from dataclasses import dataclass
from typing import Tuple

# 引入配置中的模拟数据范围
from config import MOCK


# ============================================================
# VehicleInputs — 统一数据容器
# 将所有输入源的数据聚合到一个结构体中，方便传递给决策引擎
# ============================================================
@dataclass
class VehicleInputs:
    """车辆输入数据聚合体"""

    # --- 高德API ---
    route_description: str = ""        # 路线描述（如"望京到中关村"）
    traffic_condition: str = "unknown"  # 路况：unknown/smooth/slow/congested/severe

    # --- RMS ---
    driving_condition: str = "urban_smooth"  # 驾驶工况
    driver_style: str = "normal"           # 驾驶员风格：conservative/normal/aggressive

    # --- VCU网关 ---
    soc: float = 0.5            # 电池剩余电量（0~1）
    speed: float = 0.0          # 当前车速（km/h）
    gear: str = "P"             # 档位：P/N/D/R/B
    throttle_angle: float = 0.0 # 油门踏板角度（%）
    battery_temp: float = 25.0  # 电池包温度（°C）

    # --- GPS ---
    latitude: float = 39.9042   # 纬度（默认北京）
    longitude: float = 116.4074 # 经度（默认北京）

    timestamp: str = ""          # 数据采集时间戳

    def to_dict(self) -> dict:
        """将数据容器转换为字典，便于 JSON 序列化和 LLM 调用"""
        return {
            "route_description": self.route_description,
            "traffic_condition": self.traffic_condition,
            "driving_condition": self.driving_condition,
            "driver_style": self.driver_style,
            "soc": round(self.soc, 2),
            "speed": round(self.speed, 1),
            "gear": self.gear,
            "throttle_angle": round(self.throttle_angle, 1),
            "battery_temp": round(self.battery_temp, 1),
            "latitude": self.latitude,
            "longitude": self.longitude,
            "timestamp": self.timestamp,
        }


# ============================================================
# GaodeAPI — 高德路线与路况模拟
# ============================================================
class GaodeAPI:
    """
    模拟高德地图 API
    负责：获取当前驾驶路线、查询沿途路况信息
    真实场景：调用高德路径规划 + 路况查询接口
    """
    def __init__(self):
        # 路线轮播索引
        self._route_idx = 0
        # 预设的几条典型路线（北京常见通勤路线）
        self._routes = [
            "Wangjing to Zhongguancun via North 4th Ring West",
            "Guomao to Xidan via Chang'an Avenue",
            "Wudaokou to Shangdi via Xinxi Road",
            "Chaoyang Joy City to Sanlitun via Chaoyang North Road",
        ]

    def get_route_info(self) -> Tuple[str, str]:
        """
        获取当前路线和路况信息
        Returns:
            (route_description, traffic_condition)
            - route_description: 路线文字描述
            - traffic_condition: unknown/smooth/slow/congested/severe
        """
        # 轮换到下一个路线（模拟行程变化）
        self._route_idx = (self._route_idx + 1) % len(self._routes)
        # 随机生成路况（模拟实时路况变化）
        traffic = random.choice(MOCK["traffic_options"])
        return self._routes[self._route_idx], traffic


# ============================================================
# RMSSimulator — RMS驾驶工况模拟
# ============================================================
class RMSSimulator:
    """
    模拟 RMS（能量管理策略）脚本
    负责：判断当前的驾驶工况类型
    真实场景：通过车辆总线信号 + 环境感知融合得出
    """
    def __init__(self):
        # 初始工况为市区平顺
        self._current = "urban_smooth"
        self._style = "normal"

    def get_driving_condition(self) -> str:
        """
        获取当前驾驶工况
        Returns: highway_cruise / elevated_cruise / urban_congested / urban_smooth / suburban / mountain
        """
        # 30% 概率切换工况（模拟路况变化）
        if random.random() < 0.3:
            self._current = random.choice(MOCK["driving_conditions"])
        return self._current

    def get_driver_style(self) -> str:
        """
        获取当前驾驶员风格
        Returns: conservative / normal / aggressive
        """
        if random.random() < 0.1:
            self._style = random.choice(["conservative", "normal", "aggressive"])
        return self._style


# ============================================================
# VCUGateway — VCU网关模拟
# ============================================================
class VCUGateway:
    """
    模拟 VCU（Vehicle Control Unit）网关
    负责：读取车辆核心状态数据
    真实场景：通过 CAN 总线读取车辆信号
    """
    def __init__(self):
        # 初始状态：70%电量、静止、D档、25°C
        self._soc = 0.7
        self._speed = 0.0
        self._gear = "P"
        self._throttle = 0.0
        self._battery_temp = 25.0

    def read(self) -> dict:
        """
        读取车辆当前状态
        Returns:
            dict 包含 soc / speed / gear / throttle_angle / battery_temp
        """
        # SOC 随时间缓慢下降（模拟行驶耗电）
        self._soc = max(0.15, self._soc - random.uniform(0, 0.005))

        # 车速随机变化（模拟加减速）
        target_speed = random.choice([0, 20, 40, 60, 80, 100])
        self._speed = self._speed * 0.7 + target_speed * 0.3

        # 油门角度跟随车速变化
        if self._speed > 0:
            self._throttle = max(0, min(100, self._throttle + random.uniform(-5, 5)))
        else:
            self._throttle = 0.0

        # 档位逻辑：速度>1时挂D档，静止时随机P/N/D
        self._gear = "D" if self._speed >= 1 else random.choices(
            ["P", "N", "D"], weights=[0.3, 0.1, 0.6]
        )[0]

        # 电池温度缓慢波动
        self._battery_temp = max(5, min(55, self._battery_temp + random.uniform(-0.5, 1.0)))

        return {
            "soc": round(self._soc, 2),
            "speed": round(self._speed, 1),
            "gear": self._gear,
            "throttle_angle": round(self._throttle, 1),
            "battery_temp": round(self._battery_temp, 1),
        }


# ============================================================
# GPSModule — GPS定位模拟
# ============================================================
class GPSModule:
    """
    模拟 GPS 定位模块
    负责：获取当前车辆坐标（经纬度）
    真实场景：通过车载 GPS 接收器获取
    """
    def __init__(self):
        # 初始坐标：北京中心（天安门附近）
        self._lat = 39.9042
        self._lng = 116.4074

    def get_coordinates(self) -> Tuple[float, float]:
        """
        获取当前 GPS 坐标
        Returns: (latitude, longitude)
        """
        # 微小幅度的随机移动（模拟车辆行驶）
        self._lat += random.uniform(-0.001, 0.001)
        self._lng += random.uniform(-0.001, 0.001)
        return (round(self._lat, 6), round(self._lng, 6))


# ============================================================
# InputCollector — 统一数据采集器
# ============================================================
class InputCollector:
    """
    统一数据采集器
    - 聚合所有输入源（高德 / RMS / VCU / GPS）
    - 对外提供 collect() 方法返回统一的 VehicleInputs 对象
    """
    def __init__(self):
        # 初始化各数据源
        self.gaode = GaodeAPI()       # 路线路况
        self.rms = RMSSimulator()     # 驾驶工况
        self.vcu = VCUGateway()       # 车辆状态
        self.gps = GPSModule()        # GPS定位

    def collect(self) -> VehicleInputs:
        """
        执行一轮完整的数据采集
        依次从四个数据源获取信息，聚合后返回
        """
        # 1. 高德：路线 + 路况
        route, traffic = self.gaode.get_route_info()

        # 2. RMS：驾驶工况
        driving_cond = self.rms.get_driving_condition()
        driver_style = self.rms.get_driver_style()

        # 3. VCU 网关：车辆状态
        vcu_data = self.vcu.read()

        # 4. GPS：坐标
        lat, lng = self.gps.get_coordinates()

        # 聚合返回
        return VehicleInputs(
            route_description=route,
            traffic_condition=traffic,
            driving_condition=driving_cond,
            driver_style=driver_style,
            soc=vcu_data["soc"],
            speed=vcu_data["speed"],
            gear=vcu_data["gear"],
            throttle_angle=vcu_data["throttle_angle"],
            battery_temp=vcu_data["battery_temp"],
            latitude=lat,
            longitude=lng,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )