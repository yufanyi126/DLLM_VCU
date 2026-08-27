import serial
import time

# 配置（根据实际情况修改）
PORT = "COM3"
BAUD = 9600
ADDR = 0x50

# Modbus CRC 计算
def modbus_crc(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return bytes([crc & 0xFF, crc >> 8])

try:
    ser = serial.Serial(PORT, BAUD, timeout=1)
    print(f"✓ 串口 {PORT} 已打开")
except Exception as e:
    print(f"✗ 无法打开 {PORT}: {e}")
    exit()

# 发送读取加速度的Modbus指令 (地址0x50, 功能码0x03, 起始0x34, 数量3)
cmd = bytes([ADDR, 0x03, 0x00, 0x34, 0x00, 0x03])
cmd += modbus_crc(cmd)
print(f"→ 发送: {cmd.hex().upper()}")

ser.write(cmd)
time.sleep(0.1)
response = ser.read(100)
if response:
    print(f"← 收到 {len(response)} 字节: {response.hex().upper()}")
else:
    print("✗ 未收到回复！请检查：")
    print("  1. COM口号是否正确")
    print("  2. 波特率是否正确 (尝试 9600 / 115200)")
    print("  3. RS485 A/B 接线是否正确")
    print("  4. 传感器是否已供电")

ser.close()
