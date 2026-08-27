# -*- coding: utf-8 -*-
"""
【LangGraph 循环决策 Agent】
使用 LangGraph 框架构建有状态循环图，实现 20s 周期的持续决策。

图结构：
  START → collect_inputs → decide → wait_and_log → (条件边)
                                                     ├──→ collect_inputs（继续循环）
                                                     └──→ END（达到最大周期数）

关键特性：
- 有状态图：每次循环更新 cycle_count 等控制字段
- 内置 20s 等待：在 wait_and_log 节点中休眠
- 条件终止：通过 max_cycles 控制循环次数
"""

import time
import json
import sys
import os
from datetime import datetime
from typing import TypedDict, Optional, Literal

# 路径初始化（确保能找到同级模块和项目根目录的 config.py）
_this_dir = os.path.dirname(os.path.abspath(__file__))
_root_dir = os.path.dirname(_this_dir)
if _this_dir not in sys.path:
    sys.path.insert(0, _this_dir)
if _root_dir not in sys.path:
    sys.path.insert(0, _root_dir)

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from input_sources import InputCollector, VehicleInputs
from llm_client import DeepSeekClient, RuleBasedDecider
from config import DECISION_INTERVAL


# ============================================================
# VehicleState — 图的状态类型定义
# ============================================================
class VehicleState(TypedDict):
    """
    车辆决策状态 — LangGraph 图的全局状态

    包含三部分：
    1. 输入字段：从各数据源采集的原始数据
    2. 输出字段：LLM/规则引擎做出的决策
    3. 控制字段：循环计数、运行模式等
    """
    # --- 输入 ---
    route_description: str          # 路线描述
    traffic_condition: str          # 路况
    driving_condition: str          # 驾驶工况
    soc: float                      # 电池电量
    speed: float                    # 车速 (km/h)
    gear: str                       # 档位
    throttle_angle: float           # 油门开度 (%)
    battery_temp: float             # 电池温度 (°C)
    latitude: float                 # GPS纬度
    longitude: float                # GPS经度
    timestamp: str                  # 时间戳

    # --- 决策输出 ---
    energy_mode: str                # hybrid / pure_ev
    driving_mode: str               # sport / standard / eco
    reasoning: str                  # 决策理由

    # --- 控制 ---
    cycle_count: int                # 当前循环次数
    max_cycles: int                 # 最大循环次数（0=无限）
    use_llm: bool                   # 是否使用LLM
    keep_running: bool              # 是否继续运行


def build_cyclic_graph(llm_client: Optional[DeepSeekClient] = None):
    """
    构建 LangGraph 循环决策图

    Args:
        llm_client: DeepSeek API 客户端实例（None=仅使用规则引擎）

    Returns:
        编译后的 LangGraph 应用程序
    """
    # 初始化数据采集器
    collector = InputCollector()

    # ============================================================
    # Node 1: collect_inputs — 采集所有输入源数据
    # ============================================================
    def collect_inputs(state: VehicleState) -> dict:
        """从高德/RMS/VCU/GPS采集最新数据，更新状态"""
        inputs = collector.collect()
        return {
            "route_description": inputs.route_description,
            "traffic_condition": inputs.traffic_condition,
            "driving_condition": inputs.driving_condition,
            "soc": inputs.soc,
            "speed": inputs.speed,
            "gear": inputs.gear,
            "throttle_angle": inputs.throttle_angle,
            "battery_temp": inputs.battery_temp,
            "latitude": inputs.latitude,
            "longitude": inputs.longitude,
            "timestamp": inputs.timestamp,
            # 每次采集后递增循环计数
            "cycle_count": state.get("cycle_count", 0) + 1,
        }

    # ============================================================
    # Node 2: decide — LLM/规则引擎做出决策
    # ============================================================
    def decide(state: VehicleState) -> dict:
        """
        使用 LLM（优先）或规则引擎（兜底）进行决策
        优先使用 LLM，失败时自动降级到规则引擎
        """
        # 构建输入字典（仅包含决策所需的字段）
        vd = {
            k: state[k]
            for k in (
                "route_description", "traffic_condition", "driving_condition",
                "soc", "speed", "gear", "throttle_angle", "battery_temp", "timestamp",
            )
        }

        # 尝试 LLM 决策
        decision = None
        if state.get("use_llm", True) and llm_client is not None:
            decision = llm_client.decide(vd)

        # LLM 失败或未启用 → 规则兜底
        if decision is None:
            decision = RuleBasedDecider.decide(vd)

        return {
            "energy_mode": decision["energy_mode"],
            "driving_mode": decision["driving_mode"],
            "reasoning": decision["reasoning"],
        }

    # ============================================================
    # Node 3: wait_and_log — 显示结果、记录日志、等待下一个周期
    # ============================================================
    def wait_and_log(state: VehicleState) -> dict:
        """
        打印当前决策结果，记录日志文件，等待20s后继续
        """
        s = state
        # 控制台输出
        print(f"\n{'=' * 60}")
        print(f"  Cycle #{s['cycle_count']}  |  {s['timestamp']}")
        print(f"{'=' * 60}")
        print(f"  SOC:{s['soc']:.0%} Speed:{s['speed']:.1f}km/h Gear:{s['gear']}")
        print(f"  Throttle:{s['throttle_angle']:.1f}% Battery:{s['battery_temp']:.1f}C")
        print(f"  Route:{s['route_description']}")
        print(f"  Traffic:{s['traffic_condition']}  Driving:{s['driving_condition']}")
        print(f"  >> Energy:{s['energy_mode']}  Drive:{s['driving_mode']}")
        print(f"  >> Reason:{s['reasoning']}\n{'=' * 60}")

        # 写入 JSONL 日志文件
        log_entry = {
            "cycle": s["cycle_count"],
            "timestamp": s["timestamp"],
            "inputs": {"soc": s["soc"], "speed": s["speed"]},
            "decision": {
                "energy_mode": s["energy_mode"],
                "driving_mode": s["driving_mode"],
            },
        }
        with open("decision_log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

        # 如果不是最后一轮，等待指定间隔
        mc = s.get("max_cycles", 0)
        if mc == 0 or s["cycle_count"] < mc:
            print(f"  [Wait {DECISION_INTERVAL}s]\n")
            time.sleep(DECISION_INTERVAL)

        return {"keep_running": True}

    # ============================================================
    # 条件边：判断是否继续循环
    # ============================================================
    def should_continue(state: VehicleState) -> Literal["collect_inputs", "__end__"]:
        """
        检查是否达到最大循环次数
        max_cycles=0 表示无限循环
        """
        mc = state.get("max_cycles", 0)
        if mc > 0 and state["cycle_count"] >= mc:
            return "__end__"         # 终止
        return "collect_inputs"      # 继续循环

    # ============================================================
    # 构建图结构
    # ============================================================
    builder = StateGraph(VehicleState)

    # 添加三个节点
    builder.add_node("collect_inputs", collect_inputs)
    builder.add_node("decide", decide)
    builder.add_node("wait_and_log", wait_and_log)

    # 连接边：START → collect → decide → wait
    builder.add_edge(START, "collect_inputs")
    builder.add_edge("collect_inputs", "decide")
    builder.add_edge("decide", "wait_and_log")

    # 条件边：wait → 继续循环 或 结束
    builder.add_conditional_edges(
        "wait_and_log",
        should_continue,
        {"collect_inputs": "collect_inputs", "__end__": END},
    )

    # 使用内存检查点启用状态持久化
    return builder.compile(checkpointer=MemorySaver())


def create_initial_state(max_cycles: int = 0, use_llm: bool = True) -> VehicleState:
    """
    创建初始状态对象

    Args:
        max_cycles: 最大决策周期数（0=无限）
        use_llm: 是否启用 LLM 决策

    Returns:
        初始化的 VehicleState 字典
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "route_description": "",
        "traffic_condition": "unknown",
        "driving_condition": "urban_smooth",
        "soc": 0.5,
        "speed": 0.0,
        "gear": "P",
        "throttle_angle": 0.0,
        "battery_temp": 25.0,
        "latitude": 39.9042,
        "longitude": 116.4074,
        "timestamp": now,
        "energy_mode": "hybrid",
        "driving_mode": "standard",
        "reasoning": "",
        "cycle_count": 0,
        "max_cycles": max_cycles,
        "use_llm": use_llm,
        "keep_running": False,
    }