# coding:UTF-8
"""
    传感器数据读取封装
    供外部模块调用，获取最新传感器数据
"""
import time
import threading
import lib.device_model as deviceModel
from lib.data_processor.roles.jy901s_dataProcessor import JY901SDataProcessor
from lib.protocol_resolver.roles.protocol_485_resolver import Protocol485Resolver


class SensorReader:
    """WT901C485 传感器数据读取器"""

    def __init__(self, port="COM3", baud=9600, addr=0x50):
        self._lock = threading.Lock()
        self._latest_data = {}

        self.device = deviceModel.DeviceModel(
            "我的JY901",
            Protocol485Resolver(),
            JY901SDataProcessor(),
            "51_0"
        )
        self.device.ADDR = addr
        self.device.serialConfig.portName = port
        self.device.serialConfig.baud = baud

        # 注册数据更新回调
        self.device.dataProcessor.onVarChanged.append(self._on_data_update)

    def _on_data_update(self, deviceModel):
        """内部回调：每次数据更新时存储最新值"""
        with self._lock:
            self._latest_data = dict(deviceModel.deviceData)

    def open(self):
        """打开串口并启动读取"""
        self.device.openDevice()
        time.sleep(0.2)

        self._read_thread = threading.Thread(
            target=self._loop_read, args=(self.device,), daemon=True
        )
        self._read_thread.start()
        time.sleep(0.5)  # 等待首帧数据
        print("SensorReader 已启动")

    def _loop_read(self, device):
        """循环读取线程"""
        while self.device.isOpen:
            try:
                # 1. 读取 GPS 定位数据 (0x48~0x50, 9个寄存器)
                gpsVals = device.readReg(0x48, 9)
                if len(gpsVals) >= 9:
                    device.protocolResolver.parseGpsRegData(gpsVals, device)

                # 2. 读取 GPS 质量数据 (0x55~0x57: 卫星数, PDOP, HDOP)
                gpsQuality = device.readReg(0x55, 3)
                if len(gpsQuality) >= 3:
                    device.setDeviceData("SatCount", gpsQuality[0])
                    device.setDeviceData("PDOP", round(gpsQuality[1] / 100.0, 2))
                    device.setDeviceData("HDOP", round(gpsQuality[2] / 100.0, 2))

                # 3. 读取 D0Status 惯导收敛状态 (0x41)
                d0Vals = device.readReg(0x41, 1)
                if len(d0Vals) >= 1:
                    device.setDeviceData("D0Status", d0Vals[0])

                # 4. 统一同步 _latest_data（包含完整的 GPS 定位+质量+惯导状态数据）
                with self._lock:
                    self._latest_data = dict(device.deviceData)

                time.sleep(0.1)
            except Exception as e:
                print(f"读取异常: {e}")
                time.sleep(0.1)

    def close(self):
        """关闭设备"""
        self.device.closeDevice()

    def get_data(self):
        """
        获取最新一帧传感器数据
        :return: dict, 包含所有字段, 无数据时为 None
        """
        with self._lock:
            return dict(self._latest_data) if self._latest_data else None
