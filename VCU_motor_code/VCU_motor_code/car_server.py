# coding:UTF-8
"""
    car_server.py — 车载端 WebSocket 服务
    ======================================
    运行在车上电脑，负责：
    1. 调用 main.init_session() / main.step() 采集硬件数据（GPS + CAN）
    2. 作为 WebSocket 服务器，把每帧 output_data 推送给远程上位机
    3. 接收上位机下发的控制命令，调用 set_drv_mode_values() 写入 CAN
"""

import sys
import os
import asyncio
import json
import argparse
import threading
import websockets

# ===== 路径配置（与 main.py 保持一致）=====
_BASE = os.path.dirname(__file__)


from main import init_session, step, shutdown_session, USE_SIMULINK
from CANFDNET import set_drv_mode_values


def _print_signal_snapshot(output):
    """
    把一帧 output_data 中读取到的全部信号，按「名称 + 值」格式打印到控制台。
    仿真模式下信号集中在 output["simulink"] 字典里。
    """
    if not isinstance(output, dict):
        return

    # 仿真模式：全部 CAN 信号都在 simulink 字段
    signals = output.get("simulink")
    if not signals:
        # 真实 CAN 模式兜底：若将来 output 里直接带 can_signals 也可读取
        signals = output.get("can_signals")
    if not signals:
        return

    print("\n[车端] ====== 本帧读取到的信号 (名称 + 值) ======")
    for name, value in signals.items():
        print(f"[车端]   {name:32s}: {value}")
    print("[车端] ==========================================")


# ===== 默认配置 =====
# 0.0.0.0 表示监听本机所有网卡；本机 receive_demo 应连接 127.0.0.1。
WS_HOST = os.environ.get("VCU_WS_HOST", "0.0.0.0")
WS_PORT = int(os.environ.get("VCU_WS_PORT", "8765"))
# LLM 决策可能超过 websockets 默认心跳窗口；关闭自动 ping，
# 避免等待 AI 返回时被误判为连接失效。
WS_PING_INTERVAL = None
WS_PING_TIMEOUT = None
# ====================


def parse_control_command(msg):
    """
    解析上位机下发的 JSON。
    支持的动作:
      - decision:      接收 receive_demo 返回的完整 LLM 决策 packet
      - set_drv_mode:  设置驾驶/能量模式
      - ping:          心跳检测
    返回 (action, params) 或 (None, None)
    """
    try:
        cmd = json.loads(msg)
    except (json.JSONDecodeError, TypeError):
        print(f"[车端] 收到无效 JSON: {msg}")
        return None, None

    # 新格式：receive_demo 回传完整 LLM 决策 packet，车端只读取现成内容。
    if cmd.get('type') == 'decision':
        payload = cmd.get('payload', {})
        return 'decision', payload

    # 旧格式：直接下发 set_drv_mode 命令，保留兼容。
    action = cmd.get('action')
    if not action:
        print(f"[车端] 缺少 action 字段: {cmd}")
        return None, None

    return action, cmd


async def handle_control_command(action, cmd, session):
    """根据 action 执行对应的控制操作"""
    if action == 'decision':
        # recv_loop 与 step 线程并发访问 session，写共享字段加锁保护。
        _lock = session.get('_lock')
        if _lock is not None:
            with _lock:
                session['last_llm_decision'] = cmd
        else:
            session['last_llm_decision'] = cmd
        control = cmd.get('control_command') if isinstance(cmd, dict) else None

        print(f"[车端] 已接收 LLM 决策 packet: current_segment={cmd.get('current_segment') if isinstance(cmd, dict) else None}")
        if control:
            await handle_control_command(control.get('action'), control, session)

    elif action == 'set_drv_mode':
        engy = cmd.get('engy_mode')
        dyn = cmd.get('dyn_mode')
        if engy is not None and dyn is not None:
            if USE_SIMULINK:
                # 仿真模式：把控制指令转发给 Simulink 模型
                sim_client = session.get('simulink')
                if sim_client:
                    sim_client.set_drv_mode_values(engy_mode=engy, dyn_mode=dyn)
                else:
                    print("[车端] Simulink 客户端未初始化，无法下发控制")
            else:
                # 真实 CAN 模式：写入 CAN 盒
                set_drv_mode_values(engy_mode=engy, dyn_mode=dyn)
            print(f"[车端] 已执行控制: engy_mode={engy}, dyn_mode={dyn}")
        else:
            print(f"[车端] set_drv_mode 缺少 engy_mode 或 dyn_mode: {cmd}")

    elif action == 'ping':
        # 心跳，无需操作
        pass

    else:
        print(f"[车端] 未知 action: {action}")


async def handler(websocket, session):
    """
    WebSocket 连接处理函数。
    每个上位机连接会创建一个协程，同时收发数据。
    """
    peer = websocket.remote_address
    print(f"[车端] 上位机已连接: {peer}")

    # 消费 pending 数据（回复已连接状态给上位机）
    await websocket.send(json.dumps({
        "type": "handshake",
        "status": "connected",
        "message": "车端服务已就绪",
    }, ensure_ascii=False))

    # 接收控制命令（异步）
    async def recv_loop():
        try:
            async for raw_msg in websocket:
                print(f"\n[车端] ====== 收到 AI 回传 (原始) ======")
                print(f"[车端] {raw_msg}")
                print(f"[车端] ==================================")

                action, cmd = parse_control_command(raw_msg)
                if action:
                    await handle_control_command(action, cmd, session)
        except websockets.ConnectionClosed as e:
            # 断线属于联调中的常见状态，打印简短信息即可，避免刷完整异常栈。
            print(f"[车端] 上位机连接关闭: {e}")
        except asyncio.CancelledError:
            pass

    recv_task = asyncio.create_task(recv_loop())

    # 主循环：采集 CAN/GPS 数据 → 推送给上位机
    # step() 内部已把 RMS 画像计算改为后台线程，此处再用 to_thread 把整个 step
    # 移出事件循环，避免任何（如 CAN 读取/CSV 落盘）阻塞导致 recv_loop 收不到 AI 回传。
    # session 由 step(线程) 与 recv_loop(事件循环) 并发访问，用一把全局锁串行化写操作。
    _lock = session.setdefault('_lock', threading.Lock())

    try:
        while not session.get('finished'):
            output = await asyncio.to_thread(step, session)
            if output is None:
                if session.get('finished'):
                    # 发送完成通知
                    await websocket.send(json.dumps({
                        "type": "finished",
                        "message": "已到达终点",
                    }, ensure_ascii=False))
                    break
                else:
                    await asyncio.sleep(2)
                    continue

            # 推送 data frame
            await websocket.send(json.dumps({
                "type": "data",
                "payload": output,
            }, ensure_ascii=False))

            # ★ 定时（每帧）把读取到的信号按 名称+值 打印到控制台
            _print_signal_snapshot(output)

            await asyncio.sleep(2)

    except asyncio.CancelledError:
        pass
    except websockets.ConnectionClosed as e:
        # 上位机关闭或网络中断时结束当前会话，等待下一次连接。
        print(f"[车端] 上位机连接关闭: {e}")
    except Exception as e:
        print(f"[车端] 发送异常: {e}")
    finally:
        recv_task.cancel()
        try:
            await recv_task
        except asyncio.CancelledError:
            pass
        except websockets.ConnectionClosed as e:
            # recv_task 结束时连接可能已经关闭，这里只保留简短日志。
            print(f"[车端] 接收任务已随连接关闭: {e}")
        print(f"[车端] 上位机断开: {peer}")


async def car_main(origin_addr=None, dest_addr=None, port=WS_PORT):
    """
    车载端主函数：
    1. 初始化硬件（GPS + CAN + 路线）
    2. 启动 WebSocket 服务
    3. 等待上层关闭
    """
    # ① 初始化硬件
    if origin_addr is None:
        origin_addr = "106.830767,29.716379"
    if dest_addr is None:
        dest_addr = "106.834505,29.715720"

    session = init_session(origin_addr, dest_addr)
    if not session:
        print("[车端] 初始化失败，退出")
        return

    print(f"[车端] 初始化完成，等待上位机连接...")

    # ② 启动 WebSocket 服务（使用偏函数注入 session）
    async def bound_handler(websocket):
        await handler(websocket, session)

    print(f"[车端] WebSocket 服务启动: ws://{WS_HOST}:{port}")
    async with websockets.serve(
        bound_handler,
        WS_HOST,
        port,
        ping_interval=WS_PING_INTERVAL,
        ping_timeout=WS_PING_TIMEOUT,
    ):
        try:
            # 持续运行，直到 session.finished 或用户 Ctrl+C
            while not session.get('finished'):
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    # ③ 清理
    shutdown_session(session)
    print("[车端] 服务已关闭")


if __name__ == '__main__':
    # ===== 手动配置：修改下面变量即可 =====
    MY_PORT = 8765                                   # WebSocket 端口
    # MY_ORIGIN = "106.830767,29.716379"               # 起点坐标 (lng,lat)
    # MY_DEST = "106.55,29.61"                 # 终点坐标 (lng,lat)  106.834505,29.715720   "106.80,29.69"
    MY_ORIGIN = "121.204403,31.416822"  # 起点坐标 (lng,lat) '121.204403,31.416822','121.322861,31.194331'
    MY_DEST = "121.322861,31.194331"  # 终点坐标 (lng,lat)
    # True 时 main.py 发送内置测试数据，不初始化真实 GPS/CAN/高德 API。
    MY_USE_MAIN_TEST_DATA = False
    # =====================================

    if MY_USE_MAIN_TEST_DATA:
        # 直接开启 main 的测试数据开关，供本机端到端手动测试使用。
        import main
        main.MAIN_TEST_DATA_SWITCH = True

    try:
        asyncio.run(car_main(
            origin_addr=MY_ORIGIN,
            dest_addr=MY_DEST,
            port=MY_PORT,
        ))
    except KeyboardInterrupt:
        print("\n[车端] 用户中断")
