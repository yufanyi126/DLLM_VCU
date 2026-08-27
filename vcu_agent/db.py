# -*- coding: utf-8 -*-
"""Database persistence layer for VCU Agent reports.
Supports SQLite (default) with easy swap to MySQL."""
import sqlite3, json, os, threading

DB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(DB_DIR, "vcu_reports.db")

_local = threading.local()

def _get_conn():
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
    return _local.conn

def init_db():
    conn = _get_conn()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_num INTEGER NOT NULL,
            num_groups INTEGER NOT NULL,
            energy_mode TEXT NOT NULL,
            driving_mode TEXT NOT NULL,
            reasoning TEXT DEFAULT '',
            decision_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sequences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL REFERENCES reports(id),
            seq_index INTEGER NOT NULL,
            route_description TEXT DEFAULT '',
            traffic_condition TEXT DEFAULT '',
            driving_condition TEXT DEFAULT '',
            energy_mode TEXT DEFAULT '',
            driving_mode TEXT DEFAULT '',
            soc REAL DEFAULT 0.5,
            speed REAL DEFAULT 0,
            gear TEXT DEFAULT 'P',
            throttle_angle REAL DEFAULT 0,
            battery_temp REAL DEFAULT 25,
            latitude REAL DEFAULT 39.9,
            longitude REAL DEFAULT 116.4,
            timestamp TEXT DEFAULT ''
        );
    CREATE INDEX IF NOT EXISTS idx_sequences_report ON sequences(report_id);
    ''')
    conn.commit()


def save_report(group_num, num_groups, decisions, sequences):
    """decisions: list of per-sequence decision dicts, one per sequence."""
    if isinstance(decisions, dict):
        decisions = [decisions]
    if not decisions:
        return None
    conn = _get_conn()
    first_dec = decisions[0]
    cur = conn.execute(
        "INSERT INTO reports (group_num, num_groups, energy_mode, driving_mode, reasoning, decision_json, created_at) VALUES (?,?,?,?,?,?,?)",
        (group_num, num_groups,
         first_dec.get("energy_mode",""),
         first_dec.get("driving_mode",""),
         first_dec.get("reasoning",""),
         json.dumps(decisions, ensure_ascii=False),
         first_dec.get("timestamp",""))
    )
    report_id = cur.lastrowid
    for i, s in enumerate(sequences):
        dec = decisions[i] if i < len(decisions) else {}
        conn.execute(
            "INSERT INTO sequences (report_id, seq_index, route_description, traffic_condition, driving_condition, soc, speed, gear, throttle_angle, battery_temp, latitude, longitude, timestamp, energy_mode, driving_mode) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (report_id, i+1,
             s.get("route_description",""),
             s.get("traffic_condition",""),
             s.get("driving_condition",""),
             s.get("soc",0.5),
             s.get("speed",0),
             s.get("gear","P"),
             s.get("throttle_angle",0),
             s.get("battery_temp",25),
             s.get("latitude",39.9),
             s.get("longitude",116.4),
             s.get("timestamp",""),
             dec.get("energy_mode",""),
             dec.get("driving_mode",""))
        )
    conn.commit()
    return report_id

def close_db():
    if hasattr(_local, "conn") and _local.conn:
        _local.conn.close()
        _local.conn = None

def load_all_reports():
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM reports ORDER BY id ASC").fetchall()
    reports = []
    for r in rows:
        seq_rows = conn.execute("SELECT * FROM sequences WHERE report_id=? ORDER BY seq_index ASC", (r["id"],)).fetchall()
        sequences = []
        for s in seq_rows:
            sequences.append({
                "route_description": s["route_description"],
                "traffic_condition": s["traffic_condition"],
                "driving_condition": s["driving_condition"],
                "soc": s["soc"],
                "speed": s["speed"],
                "gear": s["gear"],
                "throttle_angle": s["throttle_angle"],
                "battery_temp": s["battery_temp"],
                "latitude": s["latitude"],
                "longitude": s["longitude"],
                "timestamp": s["timestamp"],
                "energy_mode": s["energy_mode"],
                "driving_mode": s["driving_mode"],
            })
        try:
            decisions_list = json.loads(r["decision_json"]) if r["decision_json"] else []
            if isinstance(decisions_list, dict):
                decisions_list = [decisions_list]
        except:
            decisions_list = []
        reports.append({
            "id": r["id"],
            "num_groups": r["num_groups"],
            "sequences": sequences,
            "decisions": decisions_list,
            "timestamp": r["created_at"],
        })
    return reports

def clear_all_reports():
    conn = _get_conn()
    conn.execute("DELETE FROM sequences")
    conn.execute("DELETE FROM reports")
    conn.commit()
