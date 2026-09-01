import socket
import threading
import time
import struct


STDE_DRV_DYN_MODE_REQ  = 1.0   # 驾驶模式请求 (0: 标准, 1: SPORT, 2: ECO)
STDE_DRV_ENGY_MODE_REQ = 0.0   # 能源模式请求 (0: HEV, 1: EV)

EFCMNT_PDLE_ACCEL         = 0.0   
HV_BATT_TEMP_AVG          = 0.0   
VITESSE_VEHICULE_ROUES    = 0.0   
POS_MONOSTABLE_LEVER      = 0.0   
HV_BATT_SOC               = 0.0   
STDE_DRV_DYN_MODE_STATE   = 0.0   
STDE_DRV_ENGY_MODE_STATE  = 0.0   

SEND_INTERVAL = 0.1  


port_5000_clients = []
port_5000_clients_lock = threading.Lock()

port_5001_clients = []
port_5001_clients_lock = threading.Lock()

ui_clients = []
ui_clients_lock = threading.Lock()

server_sockets = {}



def handle_client_5001(client_socket, address):
    global EFCMNT_PDLE_ACCEL, HV_BATT_TEMP_AVG, VITESSE_VEHICULE_ROUES
    global POS_MONOSTABLE_LEVER, HV_BATT_SOC, STDE_DRV_DYN_MODE_STATE, STDE_DRV_ENGY_MODE_STATE

    print(f"[端口 5001] Simulink 状态发送模块 {address} 已连接")
    with port_5001_clients_lock:
        port_5001_clients.append(client_socket)
        
    try:
        while True:
            data = client_socket.recv(1024)
            if not data: break

            print(f"[5001] 收到 {len(data)} 字节")

            if len(data) >= 64:
                with ui_clients_lock:
                    for ui_sock in ui_clients[:]:
                        try:
                            ui_sock.sendall(data[:64])
                        except:
                            ui_clients.remove(ui_sock)

                unpacked_data = struct.unpack('<8d', data[:64])
                
                EFCMNT_PDLE_ACCEL         = unpacked_data[0]  
                HV_BATT_TEMP_AVG          = unpacked_data[1]  
                VITESSE_VEHICULE_ROUES    = unpacked_data[2]  
                POS_MONOSTABLE_LEVER      = unpacked_data[3]  
                HV_BATT_SOC               = unpacked_data[4]  
                STDE_DRV_DYN_MODE_STATE   = unpacked_data[5]  
                STDE_DRV_ENGY_MODE_STATE  = unpacked_data[6]  
                
                      
    except Exception as e:
        print(f"\n[端口 5001] 解析出错: {e}")
    finally:
        with port_5001_clients_lock:
            if client_socket in port_5001_clients: port_5001_clients.remove(client_socket)
        client_socket.close()
        print(f"\n[端口 5001] Simulink {address} 已断开")


def simulink_data_sender():
    global STDE_DRV_DYN_MODE_REQ, STDE_DRV_ENGY_MODE_REQ
    time.sleep(1)
    
    while True:
        try:
            with port_5000_clients_lock:
                if port_5000_clients:
                    packed_data = struct.pack('<dd', STDE_DRV_DYN_MODE_REQ, STDE_DRV_ENGY_MODE_REQ)
                    for client in port_5000_clients[:]:
                        try: client.send(packed_data)
                        except: port_5000_clients.remove(client)
            time.sleep(SEND_INTERVAL) 
        except Exception as e:
            print(f"数据发送出错: {e}")
            time.sleep(1)



def start_server_5000():
    server_socket = None
    try:
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(('localhost', 5000))
        server_socket.listen(5)
        print("服务器已在端口 5000 启动（等待 Simulink 接收模块连接...）")
        server_sockets[5000] = server_socket
        
        while True:
            client_socket, address = server_socket.accept()
            print(f"\n[端口 5000] Simulink 指令接收模块 {address} 已连接")
            with port_5000_clients_lock: port_5000_clients.append(client_socket)
                
            def handle_disconnect(sock, addr):
                try:
                    while True:
                        if not sock.recv(1024): break
                except: pass
                finally:
                    with port_5000_clients_lock:
                        if sock in port_5000_clients: port_5000_clients.remove(sock)
                    sock.close()
                    print(f"\n[端口 5000] Simulink 指令接收模块 {addr} 已断开")
            threading.Thread(target=handle_disconnect, args=(client_socket, address), daemon=True).start()
    except Exception as e: print(f"端口 5000 错误: {e}")
    finally:
        if server_socket: server_socket.close()

def start_server_5001():
    server_socket = None
    try:
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(('localhost', 5001))
        server_socket.listen(5)
        print("服务器已在端口 5001 启动（等待 Simulink 发送模块连接...）")
        server_sockets[5001] = server_socket
        while True:
            client_socket, address = server_socket.accept()
            threading.Thread(target=handle_client_5001, args=(client_socket, address), daemon=True).start()
    except Exception as e: print(f"端口 5001 错误: {e}")
    finally:
        if server_socket: server_socket.close()

def start_server_6000():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('localhost', 6000))
    server.listen(5)
    print("服务器已在端口 6000 启动（等待前端画图界面连接...）")
    while True:
        client, addr = server.accept()
        with ui_clients_lock: ui_clients.append(client)
        print(f"\n[内部总线] 绘图界面 {addr} 已接入 6000 端口！")
        
        def check_ui_alive(sock):
            try:
                while sock.recv(1024): pass
            except: pass
            finally:
                with ui_clients_lock:
                    if sock in ui_clients: ui_clients.remove(sock)
                sock.close()
                print("\n[内部总线] 绘图界面已关闭。")
        threading.Thread(target=check_ui_alive, args=(client,), daemon=True).start()

def start_server_7000():
    global STDE_DRV_DYN_MODE_REQ, STDE_DRV_ENGY_MODE_REQ
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('localhost', 7000))
    server.listen(5)
    print("服务器已在端口 7000启动")
    while True:
        client, addr = server.accept()
        print(f"\nAI计算 {addr} 已接入 7000 端口！")
        def handle_algo_client(sock):
            global STDE_DRV_DYN_MODE_REQ, STDE_DRV_ENGY_MODE_REQ
            buffer = b''
            FRAME_SIZE = 16 
            try:
                while True:
                    packet = sock.recv(1024)
                    if not packet: break
                    
                    buffer += packet
                    # TCP 防粘包处理
                    while len(buffer) >= FRAME_SIZE:
                        frame = buffer[:FRAME_SIZE]
                        buffer = buffer[FRAME_SIZE:]
                        unpacked = struct.unpack('<dd', frame)
                        STDE_DRV_DYN_MODE_REQ = unpacked[0]
                        STDE_DRV_ENGY_MODE_REQ = unpacked[1]
            except Exception:
                pass
            finally:
                sock.close()
        threading.Thread(target=handle_algo_client, args=(client,), daemon=True).start()


def main():
    print("="*60)
    print("正在启动 Python - Simulink 通讯节点...")
    print("="*60)
    
    threading.Thread(target=start_server_5000, daemon=True).start()
    time.sleep(0.1)
    threading.Thread(target=start_server_5001, daemon=True).start()
    time.sleep(0.1)
    threading.Thread(target=start_server_6000, daemon=True).start()
    time.sleep(0.1)
    threading.Thread(target=start_server_7000, daemon=True).start()
    time.sleep(0.1)

    threading.Thread(target=simulink_data_sender, daemon=True).start()
    
    print("\n所有节点已就绪。按 Ctrl+C 可随时退出。")
    
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        print("\n正在关闭所有服务器...")

if __name__ == "__main__":
    main()