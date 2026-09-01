# coding:UTF-8
"""
sim_tx_512.py — 离线模拟 0x512 控制报文编码（无实车 / 无 CAN 盒）
================================================================
复刻 CANFDNET.py 中 send_v2_bsi_512_thread 的 TGL 翻转 + cantools 编码逻辑，
把"AI 决策变化 → 实际发到总线上的 0x512 8 字节 HEX"完整模拟出来。

运行:  python sim_tx_512.py
"""
import os
import cantools

# ===== 与 receive_demo.py 的 FIXED_MODE_CYCLE 保持一致（模拟 AI 每帧决策）=====
MODE_CYCLE = [
    ("hybrid", "standard"),   # engy=0, dyn=0
    ("pure_ev", "standard"),  # engy=1, dyn=0
    ("pure_ev", "sport"),     # engy=1, dyn=1
    ("pure_ev", "sport"),     # engy=1, dyn=1
    ("hybrid", "sport"),      # engy=0, dyn=1
    ("hybrid", "eco"),        # engy=0, dyn=2
]
ENERGY_MAP = {"pure_ev": 1, "ev": 1, "hybrid": 0, "hev": 0}
DRIVE_MAP = {"eco": 2, "standard": 0, "sport": 1}

DBC_PATH = os.path.join(os.path.dirname(__file__), "CANLan", "StlaAIVCU.dbc")
TX_ID = 0x512
# 发送线程 100ms/帧、AI 决策约 2s 一次 → 每轮决策之间会重复发约 20 帧，这里只展示前 2 帧
FRAMES_PER_DECISION = 2


def main():
    db = cantools.database.load_file(DBC_PATH)
    msg = db.get_message_by_frame_id(TX_ID)

    # TGL 翻转状态（与 CANFDNET.py send_v2_bsi_512_thread 完全一致）
    tgl = 0
    last_engy = None
    last_dyn = None

    print(f"DBC: {DBC_PATH}")
    print(f"报文: {msg.name} (ID=0x{TX_ID:03X})  信号: {[s.name for s in msg.signals]}\n")
    print("-" * 78)
    print(f"{'决策轮':<6}{'AI 模式':<20}{'ENGY':<5}{'DYN':<5}{'TGL':<5}  0x512 发送字节(HEX)")
    print("-" * 78)

    for round_idx in range(len(MODE_CYCLE)):
        energy, driving = MODE_CYCLE[round_idx]
        engy = ENERGY_MAP[energy]
        dyn = DRIVE_MAP[driving]

        # ---- 发送线程每帧执行的 TGL 翻转逻辑（复刻 CANFDNET.py 518-526 行）----
        changed = False
        if last_engy is not None and last_dyn is not None:
            if engy != last_engy or dyn != last_dyn:
                tgl ^= 1
                changed = True
        last_engy, last_dyn = engy, dyn

        for f in range(FRAMES_PER_DECISION):
            # ---- cantools 按 DBC 编码（复刻 CANFDNET.py 528-534 行）----
            encoded = msg.encode({
                "TBD": 0,
                "STDE_DRV_ENGY_MODE_REQ": engy,
                "STDE_DRV_DYN_MODE_REQ": dyn,
                "STDE_MODE_REQ_BIT_TGL": tgl,
            })
            payload = bytes(encoded).ljust(8, b"\x00")[:8]
            hex_str = " ".join(f"{b:02X}" for b in payload)

            if f == 0:
                mark = "← 首帧,不翻转" if round_idx == 0 else ("← 决策变化→TGL翻转" if changed else "← 决策值未变")
            else:
                mark = "← 值未变,TGL保持"
            print(f"{round_idx + 1:<6}{energy + '+' + driving:<20}{engy:<5}{dyn:<5}{tgl:<5}  {hex_str}  {mark}")
        print()


if __name__ == "__main__":
    main()
