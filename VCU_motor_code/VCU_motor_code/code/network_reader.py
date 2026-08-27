# coding:UTF-8
"""
    TCP 网络版 GPS 数据接收器

    替代 SensorReader，通过 TCP 网络接收车载端发送的 GPS 数据。
    接口与 SensorReader 兼容（open / get_data / close），
    上层调用代码（main.py 的 step()）无需任何改动。

    数据格式：每帧为一行 JSON，以换行符 \n 分隔。
    示例: {"latitude":29.716379,"longitude":106.830767,"GroundSpeed":45.5,...}\n
"""

import socket
import json
import threading
import queue


class NetworkReader:
    """
    网络版 GPS 数据接收器。

    用法:
        reader = NetworkReader(host='0.0.0.0', port=9999)
        reader.open()           # 启动 TCP Server，等待车载端连接
        while True:
            data = reader.get_data()   # 非阻塞，无新数据时返回 None
            if data:
                process(data)
        reader.close()
    """

    def __init__(self, host='0.0.0.0', port=9999):
        """
        :param host: 本机监听地址，默认 0.0.0.0（接受所有网卡连接）
        :param port: 监听端口，默认 9999
        """
        self.host = host
        self.port = port
        self._sock = None
        self._conn = None
        self._buffer = queue.Queue(maxsize=1)  # 只保留最新一帧
        self._running = False
        self._thread = None

    def open(self):
        """启动 TCP 服务端并在后台线程接收数据"""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(1)
        self._sock.settimeout(2.0)
        self._running = True
        self._thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._thread.start()
        print(f"[NetworkReader] 监听 {self.host}:{self.port}，等待车载端连接...")

    def _receive_loop(self):
        """后台线程：接受连接并持续接收数据帧"""
        while self._running:
            # ---------- 等待车载端连接 ----------
            try:
                self._conn, addr = self._sock.accept()
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    print(f"[NetworkReader] 监听异常: {e}")
                continue

            print(f"[NetworkReader] 车载端已连接: {addr}")
            self._conn.settimeout(3.0)
            buf = b""

            # ---------- 持续接收数据 ----------
            while self._running:
                try:
                    chunk = self._conn.recv(4096)
                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"[NetworkReader] 接收异常: {e}")
                    break

                if not chunk:
                    # 对端关闭连接
                    break

                buf += chunk
                # 按换行符拆帧
                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line.decode('utf-8'))
                    except json.JSONDecodeError as e:
                        print(f"[NetworkReader] JSON 解析失败: {e}, 原始数据: {line[:100]}")
                        continue

                    # 只保留最新一帧（覆盖旧数据）
                    if self._buffer.full():
                        try:
                            self._buffer.get_nowait()
                        except queue.Empty:
                            pass
                    try:
                        self._buffer.put_nowait(data)
                    except queue.Full:
                        pass

            # 连接断开
            print("[NetworkReader] 车载端断开，等待重连...")
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    def get_data(self):
        """
        非阻塞获取最新一帧 GPS 数据。

        :return: dict（与 SensorReader.get_data() 格式一致）或 None
        """
        try:
            return self._buffer.get_nowait()
        except queue.Empty:
            return None

    def close(self):
        """关闭 TCP Server 并释放资源"""
        self._running = False
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        print("[NetworkReader] 已关闭")
