# coding:UTF-8
"""
    receive_demo.py — 远程上位机 AI 决策程序
    ==========================================
    运行在远程上位机负责：
    1. 通过 WebSocket 连接车载端 car_server.py，接收实时 output_data
    2. 分析 data frame（车速、电量、路况等），运行 AI 决策逻辑
    3. 下发控制命令（驾驶模式 / 能量模式）给车载端

    网络前提：
        车载端和上位机需要能网络互通（VPN / 公网 / 内网穿透）
"""

import sys
import os
import asyncio
import json
import re
from datetime import datetime
import websockets


# ===== 网络与运行开关集中配置（部署时优先通过环境变量覆盖）=====
# 车端 WebSocket 服务地址；receive_demo 主动连接该 IP 来获取车辆侧 output_data。
# 本机手动测试连接 car_server；接入真实车辆时设置 VCU_CAR_HOST 为车端 IP。
DEFAULT_CAR_HOST = os.environ.get("VCU_CAR_HOST", "127.0.0.1")
DEFAULT_CAR_PORT = int(os.environ.get("VCU_CAR_PORT", "8765"))
# 本机 IP 探测只用于日志展示，不作为车端连接地址。
LOCAL_IP_PROBE_HOST = os.environ.get("VCU_LOCAL_IP_PROBE_HOST", "192.0.2.1")
LOCAL_IP_FALLBACK = os.environ.get("VCU_LOCAL_IP_FALLBACK", "127.0.0.1")
# 测试开关：开启后只跑仿真数据和 Agent，阻断真实 WebSocket 车辆通信。
RECEIVE_TEST_SWITCH = os.environ.get("VCU_RECEIVE_TEST", "0").lower() in ("1", "true", "yes", "on")
# 运行时连接配置统一从这里读取，避免文件不同位置出现多套 IP。
CAR_HOST = DEFAULT_CAR_HOST
CAR_PORT = DEFAULT_CAR_PORT
# LLM/DeepSeek 调用耗时不可控，客户端也关闭自动 ping，避免等待决策时断线。
WS_PING_INTERVAL = None
WS_PING_TIMEOUT = None
# 决策模式开关：0=调用 LLM，1=固定输出“混动 + 舒适”。
# 也可以通过环境变量 VCU_FIXED_DECISION 覆盖该值。
FIXED_DECISION_SWITCH = 1  #int(os.environ.get("VCU_FIXED_DECISION", "0"))
# 现有车端协议中，舒适驾驶模式对应 standard。
FIXED_ENERGY_MODE = "hybrid"    #hybrid
FIXED_DRIVING_MODE = "standard" #standard
# =========================================


def get_local_ip():
    """获取本机局域网 IPv4；无法探测时回退到回环地址。"""
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect((LOCAL_IP_PROBE_HOST, 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return LOCAL_IP_FALLBACK


LOCAL_IP = get_local_ip()


def is_receive_test_enabled(argv=None):
    """集中判断 receive_demo 测试开关，避免测试逻辑散落在主流程里。"""
    argv = argv if argv is not None else sys.argv
    # 命令行关闭开关优先级最高，便于环境变量开启时临时运行真实通信。
    if any(arg in argv for arg in ("--no-test", "--normal")):
        return False
    # 支持旧的 --test，也支持语义更明确的 --receive-test。
    return RECEIVE_TEST_SWITCH or any(arg in argv for arg in ("--test", "--receive-test"))


def _project_root():
    """Return the repository root so receive_demo.py can reuse vcu_agent modules."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _parse_number(value, default=0.0):
    """Extract a number from strings like '78.5%' or '35C'."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else default


def _soc_to_ratio(value, default=0.5):
    """The decision agent expects SOC in 0.0-1.0, while CAN payload uses percent."""
    soc = _parse_number(value, default * 100)
    return soc / 100 if soc > 1 else soc


def validate_output_data(output):
    """Validate main.py output_data before sending it into the AI decision flow."""
    if not isinstance(output, dict):
        return False, "payload must be a dict"

    route_summary = output.get("route_summary")
    if not isinstance(route_summary, dict):
        return False, "payload.route_summary must be a dict"

    segments = output.get("segments")
    if not isinstance(segments, list):
        return False, "payload.segments must be a list"
    if not segments:
        return False, "payload.segments is empty"

    current_segment = route_summary.get("current_segment")
    if current_segment is None:
        return False, "route_summary.current_segment is None"
    if not isinstance(current_segment, int):
        return False, "route_summary.current_segment must be int or None"
    if current_segment < 1 or current_segment > len(segments):
        return False, f"route_summary.current_segment out of range: {current_segment}"

    for index, seg in enumerate(segments, start=1):
        if not isinstance(seg, dict):
            return False, f"segments[{index}] must be a dict"
        if "sequence" not in seg:
            return False, f"segments[{index}].sequence is missing"
        if "status" not in seg:
            return False, f"segments[{index}].status is missing"
    return True, ""


def _traffic_to_agent(value):
    """Normalize Chinese/English traffic text to the decision agent vocabulary."""
    text = str(value or "").lower()
    if any(k in text for k in ("严重", "severe")):
        return "severe"
    if any(k in text for k in ("拥堵", "堵", "congested")):
        return "congested"
    if any(k in text for k in ("缓行", "慢", "slow")):
        return "slow"
    if any(k in text for k in ("畅通", "smooth")):
        return "smooth"
    return "unknown"


def _driving_condition_for_segment(seg):
    """Infer a route condition for segments that do not carry RMS driving labels."""
    text = f"{seg.get('route', '')} {seg.get('instruction', '')}".lower()
    traffic = _traffic_to_agent(seg.get("traffic"))
    if any(k in text for k in ("高速", "快速", "express", "highway")):
        return "highway_cruise"
    if traffic in ("congested", "severe"):
        return "urban_congested"
    return "urban_smooth"


def _current_segment(output):
    """Find the current segment using route_summary.current_segment."""
    current_seg = output.get("route_summary", {}).get("current_segment")
    segments = output.get("segments", [])
    if current_seg is None or current_seg < 1 or current_seg > len(segments):
        return None, current_seg
    return segments[current_seg - 1], current_seg


def _fallback_snapshot(output):
    """Use current live CAN values as prediction defaults for pending route segments."""
    current, _ = _current_segment(output)
    current = current or {}
    return {
        "soc": current.get("soc") or "50%",
        "speed": current.get("speed") if current.get("speed") is not None else 0,
        "gear": current.get("gear") or "D",
        "throttle": current.get("throttle") or "0%",
        "battery": current.get("battery") or "25",
    }


def segment_to_agent_input(seg, output):
    """Convert one main.py segment into the normalized decision-agent input schema."""
    fallback = _fallback_snapshot(output)
    # Pending segments have no live CAN/GPS values, so use the current vehicle
    # snapshot while keeping their own route, instruction, distance, and traffic.
    route_text = seg.get("route") or seg.get("instruction") or f"segment {seg.get('sequence', '')}"
    return {
        "route_description": route_text,
        "traffic_condition": _traffic_to_agent(seg.get("traffic")),
        "driving_condition": _driving_condition_for_segment(seg),
        "driver_style": "normal",
        "soc": _soc_to_ratio(seg.get("soc") or fallback["soc"]),
        "speed": float(seg.get("speed") if seg.get("speed") is not None else fallback["speed"]),
        "gear": seg.get("gear") or fallback["gear"],
        "throttle_angle": _parse_number(seg.get("throttle") or fallback["throttle"], 0.0),
        "battery_temp": _parse_number(seg.get("battery") or fallback["battery"], 25.0),
        "latitude": 0.0,
        "longitude": 0.0,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }


def _local_decide(state):
    """直接导入 vcu_agent 决策模块，不经过本机 HTTP/Web 服务。"""
    root = _project_root()
    agent_dir = os.path.join(root, "vcu_agent")
    for path in (root, agent_dir):
        if path not in sys.path:
            sys.path.insert(0, path)
    from llm_stdlib import llm_decide, rule_decide, API_KEY

    decision = llm_decide(state) if "your-deepseek" not in API_KEY else None
    return decision if decision is not None else rule_decide(state)


def call_decision_agent(state):
    """
    根据开关选择 LLM 决策或固定决策。

    接收数据和决策 packet 回传流程不受影响；开关只控制决策内容来源。
    """
    if FIXED_DECISION_SWITCH == 1:
        return {
            "energy_mode": FIXED_ENERGY_MODE,
            "driving_mode": FIXED_DRIVING_MODE,
            # 基准: receive_demo.py decision_to_control() → hybrid=0, standard=0
            "energy_signal": 0,
            "drive_signal": 0,
            "reasoning": "[Fixed] 固定输出：混动 + 舒适（standard）",
        }

    # 开关为 0 时恢复原有 LLM/规则兜底决策。
    return _local_decide(state)


def decide_all_segments(output):
    """Run one AI decision for every segment in the current full-route payload."""
    decisions = []
    for seg in output.get("segments", []):
        state = segment_to_agent_input(seg, output)
        decision = call_decision_agent(state)
        decisions.append({
            "sequence": seg.get("sequence"),
            "route": seg.get("route"),
            "status": seg.get("status"),
            "input": state,
            "decision": decision,
        })
    return decisions


def decision_to_control(decision):
    """Map AI mode names to car_server.py set_drv_mode command values."""
    energy = decision.get("energy_mode")
    drive = decision.get("driving_mode")

    energy_map = {"pure_ev": 1, "ev": 1, "hybrid": 0, "hev": 0}
    drive_map = {"eco": 2, "standard": 0, "sport": 1}

    if energy not in energy_map or drive not in drive_map:
        return None

    return {
        "action": "set_drv_mode",
        "engy_mode": energy_map[energy],
        "dyn_mode": drive_map[drive],
    }


def compact_route_decisions(route_decisions):
    """Keep the per-segment AI results in a small JSON-safe shape."""
    compact = []
    for item in route_decisions:
        decision = item.get("decision", {})
        compact.append({
            "sequence": item.get("sequence"),
            "status": item.get("status"),
            "energy_mode": decision.get("energy_mode"),
            "driving_mode": decision.get("driving_mode"),
            "energy_signal": decision.get("energy_signal"),
            "drive_signal": decision.get("drive_signal"),
            "reasoning": decision.get("reasoning", ""),
        })
    return compact


def emit_decision_packet(packet, prefix="[AI]"):
    """Print the full LLM decision packet to the terminal in one place."""
    if not packet:
        print(f"{prefix} decision packet is empty")
        return
    print(f"{prefix} full LLM decision packet:")
    print(json.dumps(packet, ensure_ascii=False, indent=2))


def build_decision_packet(output):
    """
    Build a standalone decision packet that can be copied and reused directly.

    The packet keeps the raw per-segment LLM decisions and the current control
    command together, so the receiver only needs to read the returned content.
    """
    valid, reason = validate_output_data(output)
    if not valid:
        print(f"[AI] invalid output_data, skip decision: {reason}")
        return None

    # main.py 每帧发送完整 output_data。AI 对全路段逐段决策，但只把当前路段
    # 的控制命令下发给车端，避免提前应用未来路段的模式。
    current, current_seg = _current_segment(output)
    if current is None:
        print("[AI] no current segment, skip decision")
        return None

    route_decisions = decide_all_segments(output)
    print("[AI] full-route decisions:")
    for item in route_decisions:
        d = item["decision"]
        print(f"  segment {item['sequence']} [{item['status']}] "
              f"{d.get('energy_mode')} + {d.get('driving_mode')} | {d.get('reasoning', '')}")

    current_decision = next(
        (item["decision"] for item in route_decisions if item["sequence"] == current.get("sequence")),
        None,
    )
    if not current_decision:
        return None

    cmd = decision_to_control(current_decision)
    packet = {
        "type": "decision",
        "source": "receive_demo",
        "route_summary": output.get("route_summary", {}),
        "current_segment": current_seg,
        "current_sequence": current.get("sequence"),
        "current_decision": current_decision,
        "route_decisions": compact_route_decisions(route_decisions),
        "control_command": cmd,
    }
    emit_decision_packet(packet, prefix=f"[AI] segment {current_seg}")
    return packet


def ai_decision(output):
    """Backward-compatible wrapper that returns the full decision packet."""
    return build_decision_packet(output)


def run_receive_test():
    """测试标志：用仿真全路段数据验证 Agent，不连接车端也不发送控制命令。"""
    test_payload = {
        "route_summary": {"current_segment": 1, "progress_pct": 0.0},
        "segments": [
            {"sequence": 1, "route": "测试城市道路", "instruction": "直行",
             "distance_m": 500, "traffic": "畅通", "status": "current",
             "speed": 35, "soc": "78.5%", "gear": "D", "throttle": "25%",
             "battery": "35C"},
            {"sequence": 2, "route": "测试快速路", "instruction": "保持直行",
             "distance_m": 1200, "traffic": "缓行", "status": "pending"},
        ],
    }
    print(f"[TEST] 本机 IP: {LOCAL_IP}")
    print("[TEST] Agent 调用方式: 直接导入 vcu_agent.llm_stdlib")
    print("[TEST] 已阻断真实 WebSocket 发送，仅测试接收数据和 Agent 输出")
    valid, reason = validate_output_data(test_payload)
    if not valid:
        print(f"[TEST] 仿真 output_data 结构错误: {reason}")
        return []
    packet = build_decision_packet(test_payload)
    emit_decision_packet(packet, prefix="[TEST]")
    return packet


async def host_main(host=CAR_HOST, port=CAR_PORT):
    """
    上位机主循环：
    连接车载端 WebSocket → 接收数据 → AI 决策 → 下发控制命令
    支持断线自动重连。
    """
    ws_url = f"ws://{host}:{port}"
    reconnect_delay = 3  # 重连间隔（秒）

    while True:
        try:
            print(f"[上位机] 正在连接车载端: {ws_url} ...")
            async with websockets.connect(
                ws_url,
                ping_interval=WS_PING_INTERVAL,
                ping_timeout=WS_PING_TIMEOUT,
            ) as ws:
                print(f"[上位机] 已连接到车载端")

                async for raw_msg in ws:
                    msg = json.loads(raw_msg)
                    msg_type = msg.get('type')

                    if msg_type == 'handshake':
                        print(f"[上位机] 握手成功: {msg.get('message', '')}")

                    elif msg_type == 'data':
                        payload = msg.get('payload', {})
                        valid, reason = validate_output_data(payload)
                        if not valid:
                            print(f"[上位机] output_data 结构错误，跳过本帧: {reason}")
                            continue

                        route_summary = payload.get('route_summary', {})
                        print(f"\n[上位机] 收到数据帧 | "
                              f"路段 {route_summary.get('current_segment')} | "
                              f"进度 {route_summary.get('progress_pct')}% | "
                              f"剩余 {route_summary.get('remaining_km')}km")

                        # LLM 网络调用可能耗时较长；转入工作线程，避免阻塞 WebSocket
                        # 事件循环，从而保证连接期间仍能处理收发。
                        packet = await asyncio.to_thread(build_decision_packet, payload)
                        if packet:
                            emit_decision_packet(packet, prefix="[上位机]")
                            await ws.send(json.dumps({
                                "type": "decision",
                                "payload": packet,
                            }, ensure_ascii=False))
                            print("[上位机] 已回传 LLM 决策 packet")

                    elif msg_type == 'finished':
                        print(f"\n[上位机] 车载端已到达终点: {msg.get('message', '')}")
                        return  # 正常退出

                    else:
                        print(f"[上位机] 未知消息类型: {msg_type}, 内容: {msg}")

        except websockets.ConnectionClosed as e:
            print(f"[上位机] 连接断开: {e}")
            print(f"[上位机] {reconnect_delay} 秒后重连...")
            await asyncio.sleep(reconnect_delay)

        except (ConnectionRefusedError, OSError) as e:
            print(f"[上位机] 连接失败: {e}")
            print(f"[上位机] {reconnect_delay} 秒后重连...")
            await asyncio.sleep(reconnect_delay)

        except asyncio.CancelledError:
            break


if __name__ == '__main__':
    # 测试开关开启时只验证仿真数据和 Agent，不进入真实车辆通信循环。
    if is_receive_test_enabled():
        run_receive_test()
        raise SystemExit(0)

    print("=" * 60)
    print("  远程上位机 AI 决策程序")
    print(f"  车端 WebSocket: ws://{CAR_HOST}:{CAR_PORT}")
    print("  决策 Agent: 直接导入 vcu_agent.llm_stdlib")
    print("=" * 60)

    try:
        asyncio.run(host_main(host=CAR_HOST, port=CAR_PORT))
    except KeyboardInterrupt:
        print("\n[上位机] 用户中断")
