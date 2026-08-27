import socket
import threading
import struct
import time
CASE_CHOOSE = "HEV"
DRIVE_MODES = [0.0, 1.0, 2.0]

def tcp_connect():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    global STDE_DRV_DYN_MODE_REQ, STDE_DRV_ENGY_MODE_REQ
    while True:
        try:
            print("尝试连接指令网关 (TCP 7000)...")
            sock.connect(('127.0.0.1', 7000))
            print("连接成功！算法控制权已接管。")
            break
        except ConnectionRefusedError:
            time.sleep(1)

    
    if CASE_CHOOSE == "EV":
        POWER_MODES = [0.0]        # EV
    elif CASE_CHOOSE == "HEV":
        POWER_MODES = [1.0]        #  HE
    else:
        POWER_MODES = [0.0, 1.0]   # HEV 和 EV 
    test_cases = []
    for p in POWER_MODES:
        for d in DRIVE_MODES:
            test_cases.append((d, p))
    case_index = 0  
    try:
        while True:
            current_case = test_cases[case_index]
            STDE_DRV_DYN_MODE_REQ = current_case[0]
            STDE_DRV_ENGY_MODE_REQ = current_case[1]
            packed_data = struct.pack('<dd', STDE_DRV_DYN_MODE_REQ, STDE_DRV_ENGY_MODE_REQ)
            sock.sendall(packed_data)
            time.sleep(15.0) 
            case_index = (case_index + 1) % len(test_cases)
            
    except KeyboardInterrupt:
        print("自动化测试已手动终止。")
    except Exception as e:
        print(f" 通讯中断: {e}")
    finally:
        sock.close()




def main():
    print("="*60)
    print(" AI已启动 ")
    print("="*60)
    threading.Thread(target=tcp_connect, daemon=True).start()
    try:
        while True:
            time.sleep(1) 
    except KeyboardInterrupt:
        print("\n收到退出指令，AI 测试已手动终止。")

if __name__ == "__main__":
    main()
