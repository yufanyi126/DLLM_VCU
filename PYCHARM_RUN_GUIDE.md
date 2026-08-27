# PyCharm 运行指南

本目录是可移植代码包。不要复用其他电脑复制来的 `.venv`，在 PyCharm 中为本目录重新创建解释器。

## 1. 打开项目

在 PyCharm 中选择：

```text
File -> Open
```

打开本文件所在的项目根目录。

## 2. 创建解释器

进入：

```text
File -> Settings -> Project -> Python Interpreter
```

选择：

```text
Add Interpreter -> Add Local Interpreter -> Virtualenv Environment -> New
```

解释器位置建议放在项目根目录下：

```text
.venv
```

Base interpreter 选择本机已安装的 Python。

## 3. 安装依赖

在 PyCharm 底部 Terminal 中执行：

```powershell
python -m pip install -r requirements.txt
```

如果 `python` 不是 PyCharm 当前解释器，可使用：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 4. 运行 receive_demo

Run Configuration 配置：

```text
Script path: VCU_motor_code\VCU_motor_code\receive_demo.py
Working directory: VCU_motor_code\VCU_motor_code
```

本机联调时默认连接：

```text
127.0.0.1:8765
```

真实车端连接时，在 Environment variables 中设置：

```text
VCU_CAR_HOST=车端IP;VCU_CAR_PORT=8765
```

## 5. 运行 car_server

Run Configuration 配置：

```text
Script path: VCU_motor_code\VCU_motor_code\car_server.py
Working directory: VCU_motor_code\VCU_motor_code
```

本机端到端测试时，`car_server.py` 底部保持：

```python
MY_USE_MAIN_TEST_DATA = True
```

接入真实 GPS/CAN/高德流程时改为：

```python
MY_USE_MAIN_TEST_DATA = False
```

## 6. 推荐测试顺序

本机手动测试：

```text
先运行 car_server.py
再运行 receive_demo.py
```

真实车端：

```text
车端电脑运行 car_server.py
上位机电脑运行 receive_demo.py
```
