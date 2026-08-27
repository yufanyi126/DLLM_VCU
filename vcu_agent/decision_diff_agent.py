# -*- coding: utf-8 -*-
"""【决策差异预估 Agent — 独立文件】在决策进入 LLM/规则前比较当前与上次输入，预估输出是否变化"""

import json, copy, os, sys
from typing import Optional

_this_dir = os.path.dirname(os.path.abspath(__file__))
_root_dir = os.path.dirname(_this_dir)
for p in [_this_dir, _root_dir]:
    if p not in sys.path: sys.path.insert(0, p)

# 7 个关键决策字段
KEY_FIELDS = [
    "soc", "speed", "traffic_condition", "driving_condition",
    "driver_style", "throttle_angle", "battery_temp",
]

# 数值型字段的变化敏感度阈值
THRESHOLDS = {
    "soc": 0.05,           # SOC 变化 ≥5% 视为变更
    "speed": 10.0,         # 车速变化 ≥10 km/h 视为变更
    "throttle_angle": 15.0, # 油门变化 ≥15% 视为变更
    "battery_temp": 5.0,   # 温度变化 ≥5°C 视为变更
}


class DecisionDiffAgent:
    """在决策前比较当前输入与上次输入，预估输出是否变化"""

    def __init__(self, use_llm: bool = False):
        self._last_input: Optional[dict] = None
        self._last_decision: Optional[dict] = None
        self.use_llm = use_llm

    # ----------------------------------------------------------
    # 公共接口
    # ----------------------------------------------------------
    def predict_change(self, current_input: dict) -> dict:
        """预估当前输入相对于上次是否会产生变化"""
        if self._last_input is None:
            return {
                "prediction": "CHANGE", "changed_fields": [], "field_details": {},
                "reasoning": "首次决策，无历史数据，默认 CHANGE",
                "confidence": "medium", "is_first_decision": True,
            }
        old_f = self._extract_key_fields(self._last_input)
        new_f = self._extract_key_fields(current_input)
        changed_fields, field_details = [], {}
        for field in KEY_FIELDS:
            new_val = new_f.get(field)
            if new_val is None: continue
            old_val = old_f.get(field)
            if self._is_changed(field, old_val, new_val):
                changed_fields.append(field)
                field_details[field] = {"old": old_val, "new": new_val, "delta": self._calc_delta(field, old_val, new_val)}
        if not changed_fields:
            return {
                "prediction": "SAME", "changed_fields": [], "field_details": {},
                "reasoning": "所有关键字段无明显变化，决策预期不变",
                "confidence": "high", "is_first_decision": False,
            }
        return self._llm_analyze(old_f, new_f, changed_fields, field_details) if self.use_llm \
            else self._rule_analyze(old_f, new_f, changed_fields, field_details)

    def record_decision(self, input_state: dict, decision: dict):
        """记录本次决策的输入和输出供下次比较"""
        self._last_input = copy.deepcopy(input_state)
        self._last_decision = copy.deepcopy(decision)

    def reset(self):
        self._last_input = self._last_decision = None

    @property
    def last_input(self) -> Optional[dict]: return self._last_input
    @property
    def last_decision(self) -> Optional[dict]: return self._last_decision

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------
    def _extract_key_fields(self, state: dict) -> dict:
        return {f: state[f] for f in KEY_FIELDS if f in state and state[f] is not None}

    def _is_changed(self, field: str, old_val, new_val) -> bool:
        if old_val is None and new_val is not None: return True
        if old_val is not None and new_val is None: return True
        if type(old_val) != type(new_val): return True
        if field in THRESHOLDS:
            try: return abs(float(new_val) - float(old_val)) >= THRESHOLDS[field]
            except (ValueError, TypeError): return str(old_val) != str(new_val)
        return str(old_val).strip().lower() != str(new_val).strip().lower()

    def _calc_delta(self, field, old, new):
        if field in THRESHOLDS:
            try: return round(float(new) - float(old), 2)
            except: pass
        return f"{old} → {new}"

    def _rule_analyze(self, old_f, new_f, changed, details) -> dict:
        critical = False
        parts = []
        for field in changed:
            nv = new_f.get(field)
            if field == "soc":
                ov = old_f.get(field, 0.5)
                oc, nc = ov < 0.3 or ov > 0.7, nv < 0.3 or nv > 0.7
                if oc != nc:
                    critical = True; parts.append(f"SOC跨阈值({ov}→{nv})能量模式必定变更")
            elif field == "driving_condition":
                ov = old_f.get(field, "")
                hs, us = {"highway_cruise","mountain"}, {"urban_congested","urban_smooth"}
                if (ov in hs) != (nv in hs):
                    critical = True; parts.append(f"工况在公路/山路与城区间切换({ov}→{nv})能量模式必定变更")
            elif field == "traffic_condition":
                ov = old_f.get(field, "")
                if nv == "congested" or ov == "congested":
                    critical = True; parts.append(f"路况拥堵状态变化({ov}→{nv})影响能量模式")
            elif field == "driver_style":
                ov = old_f.get(field, "")
                if ov != nv:
                    critical = True; parts.append(f"驾驶员风格变化({ov}→{nv})影响能量与驾驶模式")
            elif field == "battery_temp":
                ov = old_f.get(field, 25)
                if (ov > 45 or ov < -5) != (nv > 45 or nv < -5):
                    critical = True; parts.append(f"电池温度越界状态变化({ov}→{nv}°C)保护逻辑触发")
            elif field == "throttle_angle":
                ov = old_f.get(field, 0)
                if (ov > 65) != (nv > 65):
                    critical = True; parts.append(f"油门跨运动模式阈值({ov}→{nv})驾驶模式必定变更")
            elif field == "speed":
                ov = old_f.get(field, 0)
                if (ov > 80) != (nv > 80):
                    parts.append(f"车速跨高速阈值80km/h({ov}→{nv}km/h)")
        if critical:
            return {"prediction": "CHANGE", "changed_fields": changed, "field_details": details,
                    "reasoning": "；".join(parts) or "存在关键性变更字段", "confidence": "high", "is_first_decision": False}
        if len(changed) >= 2:
            return {"prediction": "CHANGE", "changed_fields": changed, "field_details": details,
                    "reasoning": f"多字段同时变化({len(changed)}个)决策很可能改变", "confidence": "medium", "is_first_decision": False}
        return {"prediction": "SAME", "changed_fields": changed, "field_details": details,
                "reasoning": "变化字段为次要字段或幅度较小，决策预期不变", "confidence": "high", "is_first_decision": False}

    def _llm_analyze(self, old_f, new_f, changed, details) -> dict:
        try:
            prompt = (
                "You are a VCU diff analyzer. Predict if energy_mode(hybrid/pure_ev) or driving_mode(sport/standard/eco) will CHANGE.\n"
                f"Changed: {', '.join(changed)}\nOld: {json.dumps(old_f)}\nNew: {json.dumps(new_f)}\n"
                'Return JSON: {"prediction":"CHANGE"|"SAME","reasoning":"...","confidence":"high"|"medium"|"low"}'
            )
            import urllib.request
            from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
            api_key = DEEPSEEK_API_KEY
            if "your-deepseek" in api_key: return self._rule_analyze(old_f, new_f, changed, details)
            payload = {"model": DEEPSEEK_MODEL, "messages": [
                {"role":"system","content":"You analyze VCU state changes. Return JSON only."},
                {"role":"user","content": prompt},
            ], "temperature": 0.1, "max_tokens": 100000, "response_format": {"type": "json_object"}}
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{DEEPSEEK_BASE_URL}/v1/chat/completions",
                data=data, headers={"Content-Type":"application/json","Authorization":f"Bearer {api_key}"}, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            content = result["choices"][0]["message"]["content"].strip()
            llm_r = json.loads(content)
            return {"prediction": llm_r.get("prediction","CHANGE"), "changed_fields": changed, "field_details": details,
                    "reasoning": f"[LLM] {llm_r.get('reasoning','')}", "confidence": llm_r.get("confidence","medium"), "is_first_decision": False}
        except Exception as e:
            r = self._rule_analyze(old_f, new_f, changed, details)
            r["reasoning"] = f"[LLM Fallback] {e}. " + r["reasoning"]; r["confidence"] = "low"
            return r


# 全局单例
_global_agent: Optional[DecisionDiffAgent] = None
def get_diff_agent(use_llm: bool = False) -> DecisionDiffAgent:
    global _global_agent
    if _global_agent is None: _global_agent = DecisionDiffAgent(use_llm=use_llm)
    return _global_agent

def predict_and_record(current_input: dict, decision: dict, use_llm: bool = False) -> dict:
    """一站式：先预估差异再记录本次决策"""
    a = get_diff_agent(use_llm=use_llm)
    p = a.predict_change(current_input)
    a.record_decision(current_input, decision)
    return p


if __name__ == "__main__":
    print("="*60+"\n  DecisionDiffAgent 测试\n"+"="*60)
    agent = DecisionDiffAgent()
    s1 = {"soc":0.85,"speed":60,"traffic_condition":"smooth","driving_condition":"urban_smooth","driver_style":"normal","throttle_angle":30,"battery_temp":25}
    d1 = {"energy_mode":"pure_ev","driving_mode":"eco"}
    p1 = agent.predict_change(s1)
    print(f"[1] 首次:{p1['prediction']} (期望:CHANGE)")
    agent.record_decision(s1, d1)
    p2 = agent.predict_change(s1)
    print(f"[2] 相同:{p2['prediction']} (期望:SAME)")
    s3 = {**s1, "soc":0.82}
    p3 = agent.predict_change(s3)
    print(f"[3] SOC小幅 85→82%:{p3['prediction']} (期望:SAME)")
    s4 = {**s1, "soc":0.70}
    p4 = agent.predict_change(s4)
    print(f"[4] SOC 85→70%:{p4['prediction']} fields:{p4['changed_fields']} (期望:CHANGE)")
    s5 = {**s1, "driving_condition":"highway_cruise"}
    p5 = agent.predict_change(s5)
    print(f"[5] urban→highway:{p5['prediction']} fields:{p5['changed_fields']} (期望:CHANGE)")
    s6 = {**s1, "driver_style":"aggressive"}
    p6 = agent.predict_change(s6)
    print(f"[6] normal→aggressive:{p6['prediction']} fields:{p6['changed_fields']} (期望:CHANGE)")
    s7 = {**s1, "speed":100, "throttle_angle":70, "driving_condition":"highway_cruise"}
    p7 = agent.predict_change(s7)
    print(f"[7] 多字段:{p7['prediction']} fields:{p7['changed_fields']}")
    print(f"  reasoning: {p7['reasoning']}")
    print("="*60+"\n  测试完成\n"+"="*60)
