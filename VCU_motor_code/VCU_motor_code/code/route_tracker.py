"""
路线跟踪器 (RouteTracker)
    支持 GPS 正常定位和 GPS 丢失时的航位推算。
"""
import math


class RouteTracker:
    """
    路线跟踪器：维护车辆在规划路线上的当前位置。

    工作模式：
    1. GPS 有效时：通过坐标匹配定位到最近路段
    2. GPS 丢失时：通过车速 × 时间 推算行驶距离，沿路线推进
    """

    def __init__(self, route_steps):
        """
        初始化路线跟踪器。

        参数:
            route_steps: list[dict]，由 build_route_steps() 生成
                每个元素: {
                    "step_index": int,
                    "road_name": str,
                    "instruction": str,
                    "distance": float,  # 米
                    "traffic": str,
                }
        """
        self.steps = route_steps if route_steps else []
        self.current_step_idx = 0       # 当前所在路段索引
        self.distance_in_step = 0.0      # 在当前路段中已行驶的距离（米）
        self.gps_lost = False            # GPS 是否丢失
        self._was_gps_lost = False       # 上一轮 GPS 是否丢失（状态切换检测）
        self.last_lat = None             # 最后一次有效纬度
        self.last_lon = None             # 最后一次有效经度
        self.last_valid_lat = None       # 持久保存的最后有效纬度（GPS丢失期间不变）
        self.last_valid_lon = None       # 持久保存的最后有效经度（GPS丢失期间不变）
        self.last_speed = 0.0            # km/h
        self.last_timestamp = None       # 时间戳（秒）
        self.accumulated_dist = 0.0      # GPS 丢失期间累计行驶距离（米）
        self._step_points = None         # 预解析的 polyline 坐标点，首次 _find_nearest_step 时懒加载
        # 固定总距离（米），初始化后永不改变，用于计算单调递增的进度百分比
        self.original_total_distance = sum(s['distance'] for s in self.steps) if self.steps else 0.0
        # 累计行驶距离（米），高水位标记，只增不减
        self.cumulative_distance = 0.0

    # ========== 属性访问 ==========

    def get_current_step(self):
        """返回当前路段 dict，如果已完成所有路段返回 None。"""
        if 0 <= self.current_step_idx < len(self.steps):
            return self.steps[self.current_step_idx]
        return None

    def get_progress_pct(self):
        """
        计算路线整体完成百分比（单调递增，永不倒退）。

        分母固定为初始化时的原始总距离（original_total_distance），
        分子使用高水位标记（cumulative_distance），确保只增不减。

        返回:
            float: 0.0 ~ 100.0
        """
        if not self.steps or self.original_total_distance <= 0:
            return 0.0
        # 计算当前在路线上的位置
        current_dist = sum(s['distance'] for s in self.steps[:self.current_step_idx])
        current_dist += self.distance_in_step
        # 高水位标记：确保进度永不倒退
        if current_dist > self.cumulative_distance:
            self.cumulative_distance = current_dist
        return min(self.cumulative_distance / self.original_total_distance * 100, 100.0)

    # ========== GPS 有效：坐标定位 ==========

    def update(self, lat, lon, speed, timestamp):
        """
        GPS 有效时调用：先用 GPS 坐标匹配路段，再用速度沿路线推进距离。

        参数:
            lat:       纬度
            lon:       经度
            speed:     车速 (km/h)
            timestamp: 当前时间戳（秒）
        """
        gps_recovered = self._was_gps_lost  # 本轮 GPS 是否刚恢复
        self.gps_lost = False
        self._was_gps_lost = False
        self.accumulated_dist = 0.0

        # ★ 第一步：GPS 坐标定位 → 确定当前路段（避免 GPS 静止时被速度推进拉偏后又被拉回）
        # 只有 GPS 实际移动超过 10m 时才重新匹配，防止静止抖动
        gps_moved = True
        if self.last_lat is not None and self.last_lon is not None:
            dist_moved = self._haversine(self.last_lat, self.last_lon, lat, lon)
            gps_moved = dist_moved > 10

        if gps_moved:
            new_idx = self._find_nearest_step(lat, lon)
            if new_idx != self.current_step_idx:
                self.current_step_idx = new_idx
                self.distance_in_step = 0.0

        # ★ 第二步：用时间差和速度计算本周期行驶距离（从已定位的位置向前推进）
        if self.last_timestamp is not None:
            dt = timestamp - self.last_timestamp
            if dt > 0:
                delta_m = speed * dt / 3.6
                if delta_m > 0:
                    self._advance(delta_m)

        self.last_lat = lat
        self.last_lon = lon
        self.last_valid_lat = lat   # 持久保存有效坐标
        self.last_valid_lon = lon
        self.last_speed = speed
        self.last_timestamp = timestamp

        return {"gps_recovered": gps_recovered, "action": "update"}

    def _find_nearest_step(self, lat, lon):
        """
        根据经纬度匹配最近的路段索引。
        用于 GPS 恢复后的位置纠偏。

        策略：先在当前路段 ±5 窗口内搜索（效率优先），
        若最近距离 > 500m 则退回全路线搜索（应对长时间信号丢失）。

        参数:
            lat: 纬度
            lon: 经度

        返回:
            int: 最近的路段索引
        """
        # 懒加载：首次调用时解析所有 polyline
        if self._step_points is None:
            self._build_step_points()

        if not self._step_points:
            return self.current_step_idx

        # 阶段1：在当前路段 ±5 范围内搜索
        start = max(0, self.current_step_idx - 5)
        end = min(len(self._step_points), self.current_step_idx + 6)

        best_idx = self.current_step_idx
        best_dist = float('inf')

        for i in range(start, end):
            pts = self._step_points[i]
            if not pts:
                continue
            d = min(self._haversine(lat, lon, p[0], p[1]) for p in pts)
            if d < best_dist:
                best_dist = d
                best_idx = i

        # 阶段2：若偏差过大（>500m），扩大到全路线搜索
        if best_dist > 500:
            for i in range(len(self._step_points)):
                if start <= i < end:
                    continue  # 阶段1已搜过
                pts = self._step_points[i]
                if not pts:
                    continue
                d = min(self._haversine(lat, lon, p[0], p[1]) for p in pts)
                if d < best_dist:
                    best_dist = d
                    best_idx = i

        return best_idx

    # ========== 坐标工具方法 ==========

    @staticmethod
    def _parse_polyline(polyline_str):
        """将 "lng,lat;lng,lat;..." 解析为 [(lat, lon), ...] 列表"""
        if not polyline_str:
            return []
        points = []
        for coord in polyline_str.split(';'):
            parts = coord.split(',')
            if len(parts) == 2:
                points.append((float(parts[1]), float(parts[0])))  # (lat, lon)
        return points

    def _build_step_points(self):
        """预解析所有路段的 polyline，只执行一次"""
        self._step_points = [
            self._parse_polyline(step.get('polyline', ''))
            for step in self.steps
        ]

    @staticmethod
    def _haversine(lat1, lon1, lat2, lon2):
        """计算两点间大圆距离（米）"""
        R = 6371000  # 地球半径 (m)
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    # ========== 通用距离推进 ==========

    def _advance(self, delta_m):
        """
        按给定距离（米）沿路线推进，可一次跨越多个路段。

        参数:
            delta_m: 行驶距离（米）
        """
        if delta_m <= 0:
            return

        remaining = delta_m
        # 安全上限：最多处理 len(steps) 个路段，防止异常情况
        max_steps = len(self.steps)

        for _ in range(max_steps):
            if remaining <= 0 or self.current_step_idx >= len(self.steps):
                break

            step_dist = self.steps[self.current_step_idx]['distance']
            remaining_in_step = step_dist - self.distance_in_step

            # 防御：路段距离无效时（≤0），跳过当前路段进入下一个
            if remaining_in_step <= 0:
                self.current_step_idx += 1
                self.distance_in_step = 0.0
                continue

            if remaining < remaining_in_step:
                # 还在当前路段内
                self.distance_in_step += remaining
                break
            else:
                # 走完当前路段
                remaining -= remaining_in_step
                self.current_step_idx += 1
                self.distance_in_step = 0.0

        # 兜底：若累计行驶距离已超过总距离，强制走完所有路段
        if 0 < self.original_total_distance <= self.cumulative_distance and self.current_step_idx < len(self.steps):
            self.current_step_idx = len(self.steps)
            self.distance_in_step = 0.0

    # ========== GPS 丢失：航位推算 ==========

    def dead_reckon(self, speed, timestamp):
        """
        GPS 丢失时调用：通过车速和经过的时间推算行驶距离，沿路线推进。

        参数:
            speed:     当前车速 (km/h)
            timestamp: 当前时间戳（秒）
        """
        if self.last_timestamp is None:
            # 还没有初始 GPS 定位，先初始化时间戳和速度，
            # 下次调用才有时间差可用于推算
            self.last_timestamp = timestamp
            self.last_speed = speed
            return {"gps_lost": False, "action": "dead_reckon_init",
                    "last_valid_lat": self.last_valid_lat, "last_valid_lon": self.last_valid_lon}

        gps_just_lost = not self._was_gps_lost  # 是否刚丢失
        self.gps_lost = True
        self._was_gps_lost = True

        dt = timestamp - self.last_timestamp
        if dt <= 0:
            return {"gps_lost": False, "action": "dead_reckon",
                    "last_valid_lat": self.last_valid_lat, "last_valid_lon": self.last_valid_lon}

        self.last_timestamp = timestamp

        # 距离（米）= 速度(km/h) × 时间(秒) / 3.6
        delta_m = speed * dt / 3.6
        if delta_m <= 0:
            return {"gps_lost": False, "action": "dead_reckon",
                    "last_valid_lat": self.last_valid_lat, "last_valid_lon": self.last_valid_lon}

        self.accumulated_dist += delta_m
        self._advance(delta_m)
        self.last_speed = speed

        return {"gps_lost": gps_just_lost, "action": "dead_reckon",
                "last_valid_lat": self.last_valid_lat, "last_valid_lon": self.last_valid_lon}

    # ========== 辅助方法 ==========

    def reload_steps(self, new_steps, lat=None, lon=None):
        """
        API 刷新后重新加载路段列表，通过 GPS 坐标在新路线上重新定位。

        参数:
            new_steps: 新的路段列表（与 __init__ 格式相同）
            lat:       当前纬度，用于 GPS 重新定位
            lon:       当前经度，用于 GPS 重新定位
        """
        if not new_steps:
            return

        self.steps = new_steps
        self._step_points = None  # 清除 polyline 缓存

        if lat is not None and lon is not None:
            # GPS 重新定位：在新路线上找到最近的路段
            self.current_step_idx = 0  # 临时重置，让 _find_nearest_step 做全路线搜索
            new_idx = self._find_nearest_step(lat, lon)
            self.current_step_idx = new_idx
            self.distance_in_step = 0.0
        else:
            # 无 GPS 坐标时的降级方案：保持旧索引在合理范围内
            old_idx = self.current_step_idx
            if old_idx >= len(new_steps):
                self.current_step_idx = len(new_steps) - 1
                self.distance_in_step = 0.0
            else:
                self.current_step_idx = old_idx
                self.distance_in_step = min(
                    self.distance_in_step,
                    self.steps[self.current_step_idx]['distance']
                )

        # 同步更新总距离，避免与新路线脱节
        self.original_total_distance = sum(s['distance'] for s in self.steps) if self.steps else 0.0

    def is_finished(self):
        """是否已走完所有路段。"""
        if not self.steps:
            return False  # 没有路段数据时永远不算到达终点
        # 条件1：已走完所有路段索引
        if self.current_step_idx >= len(self.steps):
            return True
        # 条件2（兜底）：累计行驶距离已达到总距离（防止路段索引异常导致永不退出）
        if self.original_total_distance > 0 and self.cumulative_distance >= self.original_total_distance:
            return True
        return False
