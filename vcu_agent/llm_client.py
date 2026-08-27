# -*- coding: utf-8 -*-
"""
【LLM客户端模块 — OpenAI SDK版】
封装 DeepSeek API 调用（兼容 OpenAI 格式）
提供 LLM 决策与规则兜底两种决策方式

依赖：openai 第三方库
如果无法安装 openai，可使用同目录下的 llm_stdlib.py（零外部依赖）
"""

import json
from typing import Optional

# 使用 OpenAI Python SDK 调用 DeepSeek API
# DeepSeek 兼容 OpenAI 的 chat completions 接口格式
from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

# ============================================================
# ?????????? / ???? ? ??????CAN???
# ============================================================
ENERGY_SIGNAL = {"hybrid": 0, "pure_ev": 1}
DRIVE_SIGNAL = {"sport": 1, "standard": 0, "eco": 2}



# ============================================================
# System Prompt — 定义 LLM 的角色和行为
# LLM 作为 VCU 能量管理专家，根据车辆状态输出结构化的 JSON 决策
# ============================================================
SYSTEM_PROMPT = """You are a VCU (Vehicle Control Unit) energy management expert. Based on current vehicle status, road conditions, and driving conditions, decide:

1. Energy mode: hybrid / pure_ev
2. Driving mode: sport / standard / eco

Rules of thumb:
- SOC < 30%: prefer hybrid; SOC > 70% with good traffic: pure_ev possible
- Congested slow traffic: prefer pure_ev; highway/climbing: prefer hybrid
- High battery temp (>50C) or low (<-10C): protect battery
- Highway cruise: standard; sport demand: sport
- Urban congestion or downhill: eco (regenerative braking)
- Mountain roads: sport or standard

Additional input: driver_style (conservative/normal/aggressive) from RMS.
- conservative: prefer pure_ev + eco
- aggressive: prefer hybrid + sport
- normal: balanced

Return JSON: {"energy_mode": "reasoning": "..."}"""


# ============================================================
# DeepSeekClient — LLM 决策客户端
# ============================================================
class DeepSeekClient:
    """
    DeepSeek API 调用封装
    使用 OpenAI SDK 与 DeepSeek 兼容接口通信
    """

    def __init__(self):
        """初始化 OpenAI 客户端，指向 DeepSeek API 端点"""
        self.client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
        )
        self.model = DEEPSEEK_MODEL

    def decide(self, vehicle_state: dict) -> Optional[dict]:
        """
        调用 DeepSeek LLM 进行决策

        Args:
            vehicle_state: 包含所有车辆状态和路况信息的字典

        Returns:
            dict: {"energy_mode", "driving_mode", "reasoning"}
            或 None（调用失败时）
        """
        try:
            # 将状态序列化为 JSON 字符串，作为 LLM 输入
            state_str = json.dumps(vehicle_state, ensure_ascii=False, indent=2)
            user_prompt = f"Current vehicle state:\n{state_str}"

            # 调用 Chat Completions API，指定 JSON 输出格式
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,        # 较低温度，保持决策一致性
                max_tokens=100000,       # 按需求放宽输出上限
                response_format={"type": "json_object"},  # 强制 JSON 输出
            )

            # 解析 LLM 返回的 JSON
            content = response.choices[0].message.content.strip()
            result = json.loads(content)

            # === 输出校验 ===
            energy = result.get("energy_mode", "hybrid")
            driving = result.get("driving_mode", "standard")
            reasoning = result.get("reasoning", "")

            # 确保输出值在合法范围内
            if energy not in ("hybrid", "pure_ev"):
                energy = "hybrid"
            if driving not in ("sport", "standard", "eco"):
                driving = "standard"

            return {
                "energy_mode": energy,
                "driving_mode": driving,
                "energy_signal": ENERGY_SIGNAL.get(energy, 0),
                "drive_signal": DRIVE_SIGNAL.get(driving, 0),
                "reasoning": reasoning,
            }

        except Exception as e:
            print(f"[LLM Error] {e}")
            return None  # 返回 None 触发规则兜底


# ============================================================
# RuleBasedDecider — 规则兜底决策引擎
# 当 LLM 不可用或调用失败时，使用预设阈值规则进行决策
# ============================================================
class RuleBasedDecider:
    """
    基于预设阈值的规则引擎
    不依赖任何外部 AI 服务，离线可用
    """

    @staticmethod
    def decide(state: dict) -> dict:
        """
        根据阈值规则做出决策

        Args:
            state: 车辆状态字典

        Returns:
            dict: {"energy_mode", "driving_mode", "reasoning"}
        """
        # === 读取输入 ===
        soc = state.get("soc", 0.5)
        speed = state.get("speed", 0)
        traffic = state.get("traffic_condition", "unknown")
        driving = state.get("driving_condition", "urban_smooth")
        style = state.get("driver_style", "normal")
        throttle = state.get("throttle_angle", 0)
        battery_temp = state.get("battery_temp", 25)

        # ============================================================
        # 能量模式决策逻辑
        # ============================================================
        # 规则1：低电量或极端温度 → 混动模式
        if soc < 0.3 or battery_temp > 45 or battery_temp < -5:
            energy_mode = "hybrid"

        # 规则2：高电量 + 好路况 → 纯电模式
        elif soc > 0.7 and traffic in ("smooth", "unknown") and driving in ("urban_smooth", "suburban"):
            energy_mode = "pure_ev"

        # 规则3：高速或山路 → 混动（需要发动机辅助）
        elif driving in ("highway_cruise", "mountain"):
            energy_mode = "hybrid"

        # 规则4：严重拥堵 → 纯电（减少排放）
        elif traffic in ("congested", "severe"):
            energy_mode = "pure_ev"

        # 规则5：默认 — 高速用混动，低速用纯电
        else:
            # 激进风格
            if style == "aggressive" and speed > 40:
                energy_mode = "hybrid"
            # 保守风格
            elif style == "conservative" and speed < 80:
                energy_mode = "pure_ev"
            # 默认
            else:
                energy_mode = "hybrid" if speed > 60 else "pure_ev"

        # ============================================================
        # 驾驶模式决策逻辑
        # ============================================================
        # 规则1：大油门开度 → 运动模式（动力需求高）
        if throttle > 70:
            driving_mode = "sport"

        # 规则2：山路 → 运动模式
        elif driving == "mountain":
            driving_mode = "sport"

        # 规则3：拥堵或低速 → 动能回收模式
        elif traffic in ("congested", "severe") or speed < 30:
            driving_mode = "eco"

        # 规则4：高速巡航 → 标准模式
        elif driving == "highway_cruise":
            driving_mode = "standard"

        # 规则5a：激进风格 → 高速时 sport
        if style == "aggressive" and speed > 80 and driving_mode == "standard":
            driving_mode = "sport"
        # 规则5b：保守风格 → eco
        elif style == "conservative" and driving_mode == "standard":
            driving_mode = "eco"
        # 规则5c：默认标准模式
        else:
            driving_mode = "standard"

        return {
            "energy_mode": energy_mode,
            "driving_mode": driving_mode,
            "energy_signal": ENERGY_SIGNAL.get(energy_mode, 0),
            "drive_signal": DRIVE_SIGNAL.get(driving_mode, 0),
            "reasoning": "[Rule-based] Threshold decision",
        }
