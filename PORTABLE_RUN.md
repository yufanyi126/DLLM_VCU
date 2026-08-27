# DLLM_VCU portable run notes

This package uses relative paths. Open the unpacked folder in VSCode or run the batch files from this folder.

## Quick checks

```bat
run_receive_test.bat
```

This verifies `receive_demo.py`, the local DeepSeek config file, and the decision packet output without connecting to the vehicle.

## Start the decision API

```bat
run_decision_server.bat
```

The service listens on:

```text
http://127.0.0.1:8080
```

## Run receive_demo against the vehicle

```bat
run_receive_real.bat
```

Default vehicle WebSocket:

```text
ws://192.168.89.96:8765
```

Override the vehicle IP before running if needed:

```bat
set VCU_CAR_HOST=192.168.89.96
run_receive_real.bat
```

Hardware-dependent scripts still require the CAN/GPS devices and their Windows drivers.
