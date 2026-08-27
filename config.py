# -*- coding: utf-8 -*-
"""
【配置文件】
VCU Agent 全局配置参数
- DeepSeek API 连接信息
- 决策阈值（SOC、温度、速度）
- 模拟数据范围
"""

import json
import os


_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
_DEEPSEEK_CONFIG_PATH = os.path.join(_PROJECT_DIR, "vcu_agent", "deepseek_config.json")


def _load_deepseek_config():
    """读取项目内相对路径配置，保证复制到其他电脑后仍可使用。"""
    try:
        with open(_DEEPSEEK_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


_LOCAL_DEEPSEEK_CONFIG = _load_deepseek_config()

# ============================================================
# DeepSeek API 配置（优先读取环境变量，未设置时使用默认占位值）
# ============================================================

# API密钥 — 环境变量优先，其次读取 vcu_agent/deepseek_config.json
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY") or _LOCAL_DEEPSEEK_CONFIG.get("api_key", "sk-your-deepseek-api-key")

# API 基础地址（DeepSeek 官方端点，兼容 OpenAI 格式）
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL") or _LOCAL_DEEPSEEK_CONFIG.get("base_url", "https://api.deepseek.com")

# 使用的模型名称
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL") or _LOCAL_DEEPSEEK_CONFIG.get("model", "deepseek-chat")

# ============================================================
# 决策周期参数
# ============================================================

# 每次决策之间的间隔（秒）— 对应 20s 循环周期
DECISION_INTERVAL = 20

# ============================================================
# 电池 SOC（State of Charge）阈值
# ============================================================

# SOC 低于此值时强制进入混动模式（保护电池避免过放）
SOC_HYBRID_THRESHOLD = 0.3

# SOC 高于此值时允许纯电模式
SOC_PURE_EV_THRESHOLD = 0.7

# ============================================================
# 电池温度阈值（摄氏度）
# ============================================================

# 电池温度过高保护阈值
BATTERY_TEMP_HIGH = 50

# 电池温度过低保护阈值
BATTERY_TEMP_LOW = -10

# ============================================================
# 车速阈值（km/h）
# ============================================================

# 高速巡航判定阈值
SPEED_HIGHWAY = 80

# 市区行驶判定阈值
SPEED_URBAN = 60

# ============================================================
# 模拟数据取值范围（用于 mock 输入源）
# ============================================================

MOCK = {
    "soc_range": (0.15, 0.95),                # SOC 模拟范围 15%~95%
    "speed_range": (0, 120),                   # 车速模拟范围 0~120 km/h
    "throttle_range": (0, 100),                # 油门开度 0%~100%
    "battery_temp_range": (5, 55),             # 电池温度 5~55°C
    "traffic_options": [                       # 路况选项
        "unknown", "smooth", "slow", "congested", "severe"
    ],
    "driving_conditions": [                    # 驾驶工况选项
        "highway_cruise", "elevated_cruise", "urban_congested",
        "urban_smooth", "suburban", "mountain"
    ],
    "gear_options": ["P", "N", "D", "R", "B"],# 档位选项
}
