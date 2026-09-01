# -*- coding: utf-8 -*-
"""
【LLM客户端模块 — 标准库版】
仅使用 Python 标准库（urllib）调用 DeepSeek API
不需要安装任何第三方包，适合受限环境

与 llm_client.py 功能相同，但零外部依赖
"""

import json
import os
import urllib.request
import urllib.error
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

# ============================================================
# API 配置（优先读取环境变量，其次读取 deepseek_config.json）
# ============================================================
API_KEY = DEEPSEEK_API_KEY
BASE_URL = DEEPSEEK_BASE_URL
MODEL = DEEPSEEK_MODEL

# ============================================================
# System Prompt — 定义 AI 助手的角色与决策规则
# ============================================================

# 双信号映射：能量模式 / 驾驶模式 → 硬件信号值（CAN总线）
ENERGY_SIGNAL = {"hybrid": 0, "pure_ev": 1}
DRIVE_SIGNAL = {"sport": 1, "standard": 0, "eco": 2}

SYSTEM_PROMPT = """You are a VCU energy management expert. Based on current vehicle status, decide:
1. Energy mode: hybrid / pure_ev
2. Driving mode: sport / standard / eco

Rules:
- SOC<30: hybrid; SOC>70 with good traffic: pure_ev
- Congested: pure_ev; highway/climbing: hybrid
- Battery >50C or <-10C: protect battery
- Highway cruise: standard; sport demand: sport
- Urban congestion or downhill: eco
- Mountain: sport or standard

Additional input: driver_style (conservative/normal/aggressive) from RMS.
- conservative: prefer pure_ev + eco
- aggressive: prefer hybrid + sport
- normal: balanced

Return JSON: {"energy_mode":"...","driving_mode":"...","reasoning":"..."}"""


def llm_decide(vehicle_state: dict) -> dict:
    """
    使用 DeepSeek API 进行决策（纯标准库实现）

    Args:
        vehicle_state: 车辆状态字典

    Returns:
        dict: {"energy_mode", "driving_mode", "reasoning"}
        或 None（API未配置或调用失败时）
    """
    # 检查 API Key 是否已配置
    if "your-deepseek" in API_KEY:
        return None  # 触发规则兜底

    # ============================================================
    # 构建请求体 — 与 OpenAI Chat Completions 格式兼容
    # ============================================================
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Current vehicle state:\n"
                    f"{json.dumps(vehicle_state, ensure_ascii=False, indent=2)}"
                ),
            },
        ],
        "temperature": 0.3,           # 较低温度，保持一致性
        # deepseek-v4-pro 会输出 reasoning_content；按需求放宽输出上限到 100000。
        "max_tokens": 100000,
        "response_format": {"type": "json_object"},  # 强制 JSON 格式
    }

    # 序列化请求数据
    data = json.dumps(payload).encode("utf-8")

    # ============================================================
    # 发送 HTTP POST 请求
    # ============================================================
    req = urllib.request.Request(
        f"{BASE_URL}/v1/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
        method="POST",
    )

    try:
        # 发送请求并解析响应（15秒超时）
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        # 提取 LLM 输出的文本
        content = result["choices"][0]["message"]["content"].strip()
        decision = json.loads(content)

        # === 输出校验 ===
        energy = decision.get("energy_mode", "hybrid")
        driving = decision.get("driving_mode", "standard")

        if energy not in ("hybrid", "pure_ev"):
            energy = "hybrid"
        if driving not in ("sport", "standard", "eco"):
            driving = "standard"

        return {
            "energy_mode": energy,
            "driving_mode": driving,
            "energy_signal": ENERGY_SIGNAL.get(energy, 0),
            "drive_signal": DRIVE_SIGNAL.get(driving, 0),
            "reasoning": decision.get("reasoning", ""),
        }

    except Exception as e:
        print(f"[LLM Error] {e}")
        return None  # 返回 None 触发规则兜底


def rule_decide(state: dict) -> dict:
    soc = state.get("soc", 0.5)
    speed = state.get("speed", 0)
    traffic = state.get("traffic_condition", "unknown")
    driving = state.get("driving_condition", "urban")

    style = state.get("driver_style", "normal")
    throttle = state.get("throttle_angle", 0)
    bt = state.get("battery_temp", 25)

    # === ????? ===
    t_map = {"light": "smooth", "moderate": "normal", "heavy": "busy", "congested": "congested"}
    traffic = t_map.get(traffic, traffic)
    d_map = {"city": "city", "urban": "urban_smooth", "highway": "highway_cruise",
             "suburban": "suburban", "mountain": "mountain"}
    driving = d_map.get(driving, driving)

    # ============================================================
    # ????
    # ============================================================
    if soc < 0.3 or bt > 45 or bt < -5:
        energy = "hybrid"
    elif soc > 0.7 and traffic in ("smooth", "unknown", "normal") and driving in ("urban_smooth", "suburban", "city"):
        energy = "pure_ev"
    elif driving in ("highway_cruise", "mountain"):
        energy = "hybrid"
    elif traffic in ("congested",):
        energy = "pure_ev"
    elif speed > 80:
        energy = "hybrid"
    else:
        energy = "pure_ev"

    if style == "aggressive" and speed > 40:
        energy = "hybrid"
    elif style == "conservative" and speed < 80 and energy != "hybrid":
        energy = "pure_ev"

    # ============================================================
    # ???????????
    # ============================================================
    # 1) ??? ? ??
    if throttle > 65:
        drive = "sport"
    # 2) ??/????
    elif driving == "mountain":
        drive = "sport"
    # 3) city ???? ? eco?????????
    elif driving == "city" and speed < 40:
        drive = "eco"
    # 4) ?? ? eco
    elif traffic in ("congested", "busy") or speed < 25:
        drive = "eco"
    # 5) ???? ? standard
    elif driving == "highway_cruise" and speed > 60:
        drive = "standard"
    # 6) ?SOC + ????? ? eco
    elif soc > 0.6 and traffic in ("smooth", "normal") and driving in ("suburban", "urban_smooth") and 40 <= speed <= 70:
        drive = "eco"
    # 7) ?????? ? sport
    elif style == "aggressive" and speed > 70:
        drive = "sport"
    # 8) ???? ? eco
    elif style == "conservative":
        drive = "eco"
    # 9) ????? ? standard
    elif speed > 50:
        drive = "standard"
    else:
        drive = "standard"

    return {
        "energy_mode": energy,
        "driving_mode": drive,
        "energy_signal": ENERGY_SIGNAL.get(energy, 0),
        "drive_signal": DRIVE_SIGNAL.get(drive, 0),
        "reasoning": "[Rule] " + driving + " " + traffic + " SOC=" + str(round(soc*100)) + "% Spd=" + str(round(speed)),
    }
