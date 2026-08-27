# -*- coding: utf-8 -*-
"""
【主入口 — CLI 模式】
启动 VCU Agent 决策引擎的命令行入口

支持参数：
  --cycles N    决策周期数（0=无限循环，默认3）
  --no-llm      禁用LLM，仅使用规则引擎

示例：
  python vcu_agent/main.py --cycles 5             # 5个周期，使用LLM
  python vcu_agent/main.py --cycles 0 --no-llm    # 无限循环，规则引擎
"""

import argparse
import sys
import os

# 路径初始化：确保能找到项目根目录的 config.py 和同级模块
_this_dir = os.path.dirname(os.path.abspath(__file__))
_root_dir = os.path.dirname(_this_dir)
if _this_dir not in sys.path:
    sys.path.insert(0, _this_dir)
if _root_dir not in sys.path:
    sys.path.insert(0, _root_dir)

from config import DEEPSEEK_API_KEY
from llm_client import DeepSeekClient
from agent import build_cyclic_graph, create_initial_state


def main():
    """
    主函数：解析命令行参数 → 初始化 → 运行循环图
    """
    # 命令行参数解析
    parser = argparse.ArgumentParser(
        description="VCU Agent - Energy Management Decision Engine (CLI)"
    )
    parser.add_argument(
        "--cycles", type=int, default=3,
        help="最大决策周期数（0=无限循环，默认3）"
    )
    parser.add_argument(
        "--no-llm", action="store_true",
        help="禁用 LLM，仅使用规则引擎决策"
    )
    args = parser.parse_args()

    # ============================================================
    # 初始化
    # ============================================================
    use_llm = not args.no_llm

    # 检查 API Key 是否已配置
    if use_llm and "your-deepseek" in DEEPSEEK_API_KEY:
        print("[Warning] 未配置有效的 DeepSeek API Key！")
        print("  自动切换为规则引擎决策。")
        print("  请通过环境变量设置 DEEPSEEK_API_KEY\n")
        use_llm = False

    # 创建 LLM 客户端（如禁用则为 None）
    llm_client = DeepSeekClient() if use_llm else None

    # 构建 LangGraph 循环图
    graph = build_cyclic_graph(llm_client)

    # 线程配置（LangGraph 检查点用）
    thread_config = {"configurable": {"thread_id": "vcu-agent-001"}}

    # ============================================================
    # 启动信息
    # ============================================================
    print("\n" + "=" * 60)
    print("  VCU Energy Management Decision Agent (LangGraph)")
    print("=" * 60)
    print(f"  LLM: {'Enabled (DeepSeek)' if use_llm else 'Disabled (Rule-based)'}")
    print(f"  Max Cycles: {'Infinite' if args.cycles == 0 else args.cycles}")
    print(f"  Interval: {20}s per cycle")
    print("=" * 60 + "\n")

    # ============================================================
    # 执行
    # ============================================================
    try:
        # 创建初始状态并启动图
        graph.invoke(
            create_initial_state(max_cycles=args.cycles, use_llm=use_llm),
            config=thread_config,
        )
        print("\n  Agent finished.\n")
    except KeyboardInterrupt:
        print("\n  Stopped by user.\n")
    except Exception as e:
        print(f"\n  Error: {e}\n")
        raise


if __name__ == "__main__":
    main()