# -*- coding: utf-8 -*-
"""
【VCU Agent CLI 启动文件】
独立于 Web 仪表盘的命令行决策工具。
支持三种输入模式:
  1. 交互式 (python launch_cli.py)
  2. 命令行参数 (python launch_cli.py --soc 0.7 --speed 80 ...)
  3. JSON 文件批量 (python launch_cli.py --file input.json)

输出: 能量模式 + 驾驶模式 + 双 CAN 信号 + 决策理由
"""

import sys, os, json, argparse
from datetime import datetime

# 将项目根目录加入 sys.path，确保能导入 vcu_agent 模块
_PROJ = os.path.dirname(os.path.abspath(__file__))
_AGENT = os.path.join(_PROJ, "vcu_agent")
for p in [_PROJ, _AGENT]:
    if p not in sys.path:
        sys.path.insert(0, p)

# ============================================================
# 自动加载环境变量：优先使用已有的环境变量，
# 若无则从项目根目录的 .env 文件读取
# ============================================================
_env_file = os.path.join(_PROJ, ".env")
if not os.environ.get("DEEPSEEK_API_KEY") and os.path.isfile(_env_file):
    try:
        with open(_env_file, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip().strip("\ufeff")
                if not _line or _line.startswith("#"):
                    continue
                if "=" in _line:
                    _k, _v = _line.split("=", 1)
                    _k = _k.strip()
                    _v = _v.strip().strip("'\"").strip()
                    if _k and _v and _v != "sk-your-deepseek-api-key":
                        os.environ[_k] = _v
    except Exception:
        pass  # .env 文件异常不影响主流程

# 从 server.py 复用决策逻辑（避免重复定义 use_llm 判断）
from llm_stdlib import llm_decide, rule_decide, API_KEY, ENERGY_SIGNAL, DRIVE_SIGNAL

# 决策引擎选择：与 web 端一致的判断逻辑
USE_LLM = not ("your-deepseek" in API_KEY)


# ============================================================
# 完整输入字段定义（12 个，与 gs() 函数一一对应）
# ============================================================
INPUT_FIELDS = [
    # (字段名, 中文名, 数据类型, 默认值, 说明)
    ("route_description", "路线描述", str, "City Center to Airport", "起点到终点的路线文本"),
    ("traffic_condition", "路况", str, "smooth",
     "可选: smooth / slow / congested / severe / unknown"),
    ("driving_condition", "驾驶工况", str, "urban_smooth",
     "可选: highway_cruise / elevated_cruise / urban_congested / urban_smooth / suburban / mountain"),
    ("driver_style", "驾驶员风格", str, "normal",
     "可选: conservative / normal / aggressive"),
    ("soc", "电量 SOC", float, 0.85, "范围 0.0 ~ 1.0 (如 70% 输入 0.7)"),
    ("speed", "车速", float, 60.0, "范围 0 ~ 150 km/h"),
    ("gear", "档位", str, "D", "可选: P / N / D / R / B"),
    ("throttle_angle", "油门踏板角度", float, 30.0, "范围 0 ~ 100 %"),
    ("battery_temp", "电池包温度", float, 25.0, "范围 -10 ~ 60 °C"),
    ("latitude", "纬度", float, 39.9042, "GPS 纬度坐标"),
    ("longitude", "经度", float, 116.4074, "GPS 经度坐标"),
    ("timestamp", "时间戳", str, "", "数据采集时间（留空自动填充当前时间）"),
]


def make_decision_cli(state: dict) -> dict:
    """
    单次决策：与 server.py 的 make_decision() 逻辑一致，
    但不包含 cycle_counter 和自动 history 记录。
    """
    d = llm_decide(state) if USE_LLM else None
    if d is None:
        d = rule_decide(state)
    # 合并输入和输出，加时间戳
    result = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **state,
        **d,
    }
    return result


def make_group_decision(groups: list) -> list:
    """
    批量决策（多组序列输入）：与 server.py 的 make_unified_decision() 逻辑一致。
    每组独立决策，不进行投票合并。
    
    Args:
        groups: 字典列表，每个字典包含完整的 12 个输入字段
    
    Returns:
        决策结果列表，每个元素增加 group_index
    """
    results = []
    for i, g in enumerate(groups):
        d = llm_decide(g) if USE_LLM else None
        if d is None:
            d = rule_decide(g)
        d["group_index"] = i + 1
        d["group_soc"] = g.get("soc", 0)
        d["group_speed"] = g.get("speed", 0)
        d["group_traffic"] = g.get("traffic_condition", "")
        d["group_driving"] = g.get("driving_condition", "")
        results.append(d)
    return results


def print_decision(dec: dict, index: int = None):
    """格式化输出一条决策结果"""
    prefix = f"[Segment #{index}] " if index is not None else ""
    
    # 能量模式
    em = dec.get("energy_mode", "?")
    es = dec.get("energy_signal", "?")
    em_color = {"hybrid": "混动", "pure_ev": "纯电"}
    
    # 驾驶模式
    dm = dec.get("driving_mode", "?")
    ds = dec.get("drive_signal", "?")
    dm_color = {"sport": "运动", "standard": "标准", "eco": "ECO"}
    
    reasoning = dec.get("reasoning", "")[:80]
    
    print(f"{'='*55}")
    if index is not None:
        print(f"  片段 #{index} 决策结果")
    else:
        print(f"  决策结果")
    print(f"{'='*55}")
    print(f"  输入参数:")
    for key in ["route_description", "traffic_condition", "driving_condition",
                 "driver_style", "soc", "speed", "gear", "throttle_angle",
                 "battery_temp", "latitude", "longitude"]:
        val = dec.get(key, "")
        print(f"    {key:20s} = {val}")
    print(f"  {'─'*50}")
    print(f"  能量模式 (Energy Mode)  : {em_color.get(em, em):>6s}  |  energy_signal = {es}")
    print(f"  驾驶模式 (Driving Mode) : {dm_color.get(dm, dm):>6s}  |  drive_signal  = {ds}")
    print(f"  决策理由 (Reasoning)    : {reasoning}")
    print(f"  时间戳                  : {dec.get('timestamp', '')}")
    print(f"{'='*55}\n")


def run_interactive():
    """交互模式：逐个字段提示用户输入"""
    print("\n" + "★" * 30)
    print("  VCU Agent 决策 — 交互输入模式")
    print("★" * 30 + "\n")
    
    state = {}
    for name, label, dtype, default, hint in INPUT_FIELDS:
        prompt_str = f"  {label} ({name}) [{default}]: "
        user_input = input(prompt_str).strip()
        
        if not user_input:
            state[name] = default
        elif dtype == float:
            try:
                state[name] = float(user_input)
            except ValueError:
                print(f"    输入无效，使用默认值 {default}")
                state[name] = default
        elif dtype == int:
            try:
                state[name] = int(user_input)
            except ValueError:
                print(f"    输入无效，使用默认值 {default}")
                state[name] = default
        else:
            state[name] = user_input
    
    # 自动填充时间戳
    if not state.get("timestamp"):
        state["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    print("\n" + "─" * 40)
    print("  正在调用决策引擎...")
    print(f"  LLM 模式: {'DeepSeek API' if USE_LLM else '规则兜底 (Rule)'}")
    print("─" * 40 + "\n")
    
    dec = make_decision_cli(state)
    print_decision(dec)


def run_with_args(args: argparse.Namespace):
    """命令行参数模式：从 argparse 解析输入"""
    state = {
        "route_description": args.route,
        "traffic_condition": args.traffic,
        "driving_condition": args.driving,
        "driver_style": args.style,
        "soc": args.soc,
        "speed": args.speed,
        "gear": args.gear,
        "throttle_angle": args.throttle,
        "battery_temp": args.battery_temp,
        "latitude": args.latitude,
        "longitude": args.longitude,
        "timestamp": args.timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    dec = make_decision_cli(state)
    print_decision(dec)


def run_with_json(filepath: str):
    """JSON 文件模式：支持单组或多组（序列）输入"""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # 如果是列表，按多组处理
    if isinstance(data, list):
        print(f"\n  检测到 {len(data)} 组序列输入，逐组决策...")
        decs = make_group_decision(data)
        for i, dec in enumerate(decs):
            print_decision(dec, index=i + 1)
        print(f"\n  共处理 {len(decs)} 组决策。\n")
        return decs
    
    # 如果是字典，按单组处理
    elif isinstance(data, dict):
        # 补全时间戳
        if not data.get("timestamp"):
            data["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        dec = make_decision_cli(data)
        print_decision(dec)
        return [dec]
    
    else:
        print("错误: JSON 文件需为对象（单组）或数组（多组）")
        sys.exit(1)


def run_quick_input():
    """快速输入模式：一行输入所有字段（空格分隔，按 INPUT_FIELDS 顺序）"""
    print("\n快速输入模式：按顺序输入以下 12 个值（空格分隔）")
    print("  " + " ".join([f"[{i+1}]{f[1]}" for i, f in enumerate(INPUT_FIELDS)]))
    print("  留空则使用全部默认值\n")
    
    line = input(">> ").strip()
    if not line:
        state = {f[0]: f[3] for f in INPUT_FIELDS}
        state["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    else:
        parts = line.split()
        state = {}
        for i, f in enumerate(INPUT_FIELDS):
            if i < len(parts) and parts[i]:
                try:
                    if f[2] == float:
                        state[f[0]] = float(parts[i])
                    else:
                        state[f[0]] = parts[i]
                except ValueError:
                    state[f[0]] = f[3]
            else:
                state[f[0]] = f[3]
        if not state.get("timestamp"):
            state["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    dec = make_decision_cli(state)
    print_decision(dec)


# ============================================================
# 主入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="VCU Agent CLI — 独立决策启动文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 交互模式
  python launch_cli.py
  
  # 快速输入模式
  python launch_cli.py --quick
  
  # 命令行参数模式
  python launch_cli.py --soc 0.6 --speed 80 --traffic smooth --driving highway_cruise --style aggressive
  
  # JSON 文件单组
  python launch_cli.py --file input.json
  
  # JSON 文件多组（序列）
  python launch_cli.py --file groups.json
  
  # JSON 文件模板生成
  python launch_cli.py --template
        """
    )
    
    parser.add_argument("--soc", type=float, default=None, help="电池电量 0.0~1.0")
    parser.add_argument("--speed", type=float, default=None, help="车速 km/h")
    parser.add_argument("--traffic", type=str, default=None, help="路况: smooth/slow/congested/severe/unknown")
    parser.add_argument("--driving", type=str, default=None, help="驾驶工况: highway_cruise/elevated_cruise/urban_congested/urban_smooth/suburban/mountain")
    parser.add_argument("--style", type=str, default=None, help="驾驶员风格: conservative/normal/aggressive")
    parser.add_argument("--gear", type=str, default=None, help="档位: P/N/D/R/B")
    parser.add_argument("--throttle", type=float, default=None, help="油门开度 0~100%")
    parser.add_argument("--battery-temp", type=float, default=None, help="电池温度 °C")
    parser.add_argument("--route", type=str, default=None, help="路线描述")
    parser.add_argument("--latitude", type=float, default=None, help="纬度")
    parser.add_argument("--longitude", type=float, default=None, help="经度")
    parser.add_argument("--timestamp", type=str, default=None, help="时间戳")
    
    parser.add_argument("--file", "-f", type=str, default=None, help="从 JSON 文件读取输入")
    parser.add_argument("--quick", "-q", action="store_true", help="快速输入模式（一行输入所有值）")
    parser.add_argument("--interactive", "-i", action="store_true", help="交互输入模式")
    parser.add_argument("--template", "-t", action="store_true", help="生成 JSON 输入模板文件")
    parser.add_argument("--output", "-o", type=str, default=None, help="输出 JSON 结果到文件")
    
    args = parser.parse_args()
    
    print(f"\n  VCU Agent CLI — 决策引擎: {'DeepSeek API' if USE_LLM else '规则 (Rule)'}")
    print(f"  API Key: {API_KEY[:8]}...{' (有效)' if USE_LLM else ' (未配置，使用规则兜底)'}")
    print()
    
    # === 生成 JSON 模板 ===
    if args.template:
        # 单组模板
        single = {f[0]: f[3] for f in INPUT_FIELDS}
        single["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tmpl_single = os.path.join(_PROJ, "input_template_single.json")
        with open(tmpl_single, "w", encoding="utf-8") as f:
            json.dump(single, f, ensure_ascii=False, indent=2)
        print(f"  单组模板已生成: {tmpl_single}")
        
        # 多组模板（3 组示例）
        groups = []
        for i in range(3):
            g = {f[0]: f[3] for f in INPUT_FIELDS}
            g["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            groups.append(g)
        tmpl_group = os.path.join(_PROJ, "input_template_groups.json")
        with open(tmpl_group, "w", encoding="utf-8") as f:
            json.dump(groups, f, ensure_ascii=False, indent=2)
        print(f"  多组模板已生成: {tmpl_group}（3 组示例，可自行增删）")
        print()
        return
    
    # === JSON 文件模式 ===
    if args.file:
        run_with_json(args.file)
        return
    
    # === 命令行参数模式（检测是否传入了任何决策参数）===
    provided_args = [a for a in [
        args.soc, args.speed, args.traffic, args.driving, args.style,
        args.gear, args.throttle, getattr(args, "battery_temp", None),
        args.route, args.latitude, args.longitude
    ] if a is not None]
    
    if provided_args:
        # 解析传入的参数，未传入的使用默认值
        defaults = {f[0]: f[3] for f in INPUT_FIELDS}
        state = {
            "route_description": args.route or defaults["route_description"],
            "traffic_condition": args.traffic or defaults["traffic_condition"],
            "driving_condition": args.driving or defaults["driving_condition"],
            "driver_style": args.style or defaults["driver_style"],
            "soc": args.soc if args.soc is not None else defaults["soc"],
            "speed": args.speed if args.speed is not None else defaults["speed"],
            "gear": args.gear or defaults["gear"],
            "throttle_angle": args.throttle if args.throttle is not None else defaults["throttle_angle"],
            "battery_temp": args.battery_temp if args.battery_temp is not None else defaults["battery_temp"],
            "latitude": args.latitude if args.latitude is not None else defaults["latitude"],
            "longitude": args.longitude if args.longitude is not None else defaults["longitude"],
            "timestamp": args.timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        dec = make_decision_cli(state)
        print_decision(dec)
        results = [dec]
    
    # === 快速输入模式 ===
    elif args.quick:
        results = run_quick_input()
    
    # === 交互模式（默认） ===
    else:
        run_interactive()
        return
    
    # === JSON 输出 ===
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"  结果已保存到: {args.output}\n")


if __name__ == "__main__":
    main()
