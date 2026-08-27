# coding:UTF-8
"""
    simulink_server.py — 纯仿真 WebSocket 服务端 (替代原 car_server.py)
    ====================================================================
    运行逻辑：
    1. 启动 TCP 6000 客户端，接收 Simulink 传来的 13个 double (104字节) 状态数据。
    2. 启动 TCP 7000 客户端，负责向 Simulink 发送控制指令。
    3. 启动 WebSocket 8765 服务端，供上位机 AI (receive_demo.py) 连接。
    4. 将 Simulink 数据包装成假的路况格式，发给 AI；并将 AI 的指令发给 Simulink。
    5. 无限循环运行，不会因为“到达终点”而退出。
"""

import asyncio
import json
import websockets
import struct

# ===== 网络端口配置 =====
WS_HOST = '0.0.0.0'
WS_PORT = 8765

TCP_SEND_HOST = '127.0.0.1'
TCP_SEND_PORT = 7000   # 往 Simulink 发控制 (2 double)

TCP_RECV_HOST = '127.0.0.1'
TCP_RECV_PORT = 6000   # 从 Simulink 收状态 (13 double)
FRAME_SIZE = 64       # 13 * 8 字节
# ========================


def parse_control_command(msg):
    """解析上位机下发的 JSON 控制命令"""
    try:
        cmd = json.loads(msg)
    except (json.JSONDecodeError, TypeError):
        return None, None
    return cmd.get('action'), cmd


async def tcp_send_worker(session):
    """TCP 下行：连接 7000 端口，随时准备向 Simulink 发送控制指令"""
    while True:
        writer = None
        try:
            print(f"[TCP-发送] 连接路由网关 {TCP_SEND_HOST}:{TCP_SEND_PORT} ...")
            _, writer = await asyncio.open_connection(TCP_SEND_HOST, TCP_SEND_PORT)
            print("[TCP-发送] 连接成功，指令通道就绪")
            
            session['tcp_send_writer'] = writer
            
            # 发送初始默认值 0.0, 0.0
            packed = struct.pack('<dd', 0.0, 0.0)
            writer.write(packed)
            await writer.drain()

            while True:
                await asyncio.sleep(1) # 保持存活
                
        except Exception as e:
            print(f"[TCP-发送] 断开或异常: {e}，2秒后重连...")
            await asyncio.sleep(2)
        finally:
            session['tcp_send_writer'] = None
            if writer:
                writer.close()


async def tcp_receiver_worker(session):
    """TCP 上行：连接 6000 端口，持续接收 Simulink 的 104 字节状态数据"""
    while True:
        reader, writer = None, None
        buffer = b''
        try:
            print(f"[TCP-接收] 连接路由网关 {TCP_RECV_HOST}:{TCP_RECV_PORT} ...")
            reader, writer = await asyncio.open_connection(TCP_RECV_HOST, TCP_RECV_PORT)
            print("[TCP-接收] 连接成功，开始接收仿真数据")

            while True:
                try:
                    packet = await asyncio.wait_for(reader.read(1024), timeout=3.0)
                except asyncio.TimeoutError:
                    # 没有数据时不要刷屏，静默等待
                    continue
                    
                if not packet:
                    print("[TCP-接收] 后端网关主动断开")
                    break

                buffer += packet
                while len(buffer) >= FRAME_SIZE:
                    frame = buffer[:FRAME_SIZE]
                    buffer = buffer[FRAME_SIZE:]

                    unpacked = struct.unpack('<8d', frame)
                    sim_time = unpacked[7]
                    
                    # 组装 Simulink 数据字典 (提取 AI 关心的字段)
                    sim_data = {
                        "EFCMNT_PDLE_ACCEL":       unpacked[0],
                        "HV_BATT_TEMP_AVG":        unpacked[1],
                        "VITESSE_VEHICULE_ROUES":  unpacked[2],
                        "POS_MONOSTABLE_LEVER":    unpacked[3],
                        "HV_BATT_SOC":             unpacked[4],
                        "STDE_DRV_DYN_MODE_STATE": unpacked[5],
                        "STDE_DRV_ENGY_MODE_STATE": unpacked[6],
                        "sim_time":                sim_time,
                    }
                    session['latest_sim_data'] = sim_data

        except Exception as e:
            print(f"[TCP-接收] 通讯异常: {e}，2秒后重试")
            await asyncio.sleep(2)
        finally:
            if writer:
                writer.close()


async def handler(websocket, session):
    """WebSocket 服务逻辑：与上位机通讯"""
    peer = websocket.remote_address
    print(f"\n[WebSocket] 上位机 AI 已接入: {peer}")

    # 1. 握手
    await websocket.send(json.dumps({
        "type": "handshake", "status": "connected", "message": "Simulink 仿真服务已就绪"
    }, ensure_ascii=False))

    # 2. 接收上位机控制指令的子任务
    async def recv_loop():
        try:
            async for raw_msg in websocket:
                action, cmd = parse_control_command(raw_msg)
                if action == 'set_drv_mode':
                    engy = cmd.get('engy_mode')
                    dyn = cmd.get('dyn_mode')
                    writer = session.get('tcp_send_writer')
                    
                    if writer and engy is not None and dyn is not None:
                        packed = struct.pack('<dd', float(dyn), float(engy))
                        writer.write(packed)
                        await writer.drain()
                        print(f"[下发] 执行 AI 控制: 能量模式={engy}, 驾驶模式={dyn}")
                    else:
                        print("[下发] 失败：TCP 网关未连接 或 参数缺失")
        except asyncio.CancelledError:
            pass

    recv_task = asyncio.create_task(recv_loop())

    # 3. 主循环：向上位机推送数据
    try:
        while True:
            sim_data = session.get('latest_sim_data')
            
            if not sim_data:
                await asyncio.sleep(0.5)
                continue
                
            # --- 伪造路段数据，让它正常做决策 ---
            output = {
                "route_summary": {
                    "current_segment": 1, 
                    "progress_pct": 50, 
                    "remaining_km": 999
                },
                "segments": [{
                    "speed": sim_data["VITESSE_VEHICULE_ROUES"],
                    "soc": f"{sim_data['HV_BATT_SOC']:.1f}%",
                    "traffic": "畅通",
                    "driving": "未知", 
                    "engy_mode": "未知",
                    "gear": "D"
                }],
                "simulink": sim_data  # AI 脚本实际依赖这个字段提取精准数据
            }

            await websocket.send(json.dumps({
                "type": "data",
                "payload": output,
            }, ensure_ascii=False))
            
            await asyncio.sleep(1)  # 每 1 秒推一次给 AI

    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception as e:
        print(f"[WebSocket] 发送异常: {e}")
    finally:
        recv_task.cancel()
        print(f"[WebSocket] 上位机 AI 断开: {peer}")


async def main():
    print("=" * 50)
    print(" 启动 Simulink 纯仿真中转服务端")
    print("=" * 50)
    
    # 全局状态字典
    session = {
        'tcp_send_writer': None,
        'latest_sim_data': None
    }

    # 启动 TCP 客户端任务
    asyncio.create_task(tcp_send_worker(session))
    asyncio.create_task(tcp_receiver_worker(session))

    # 启动 WebSocket 服务端
    async def bound_handler(websocket):
        await handler(websocket, session)

    print(f"[系统] WebSocket 监听启动: ws://{WS_HOST}:{WS_PORT}")
    async with websockets.serve(bound_handler, WS_HOST, WS_PORT):
        # 永久挂起主线程
        await asyncio.Future()  

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[系统] 用户手动终止程序")