import sys, os

path = r"C:\Users\27444\Desktop\DLLM_VCU\vcu_agent\server.py"
with open(path, "r", encoding="utf-8") as f:
    c = f.read()

# 1) Fix import - the original has no spaces after commas
old = "from llm_stdlib import llm_decide,rule_decide,API_KEY"
new = "from llm_stdlib import llm_decide,rule_decide,API_KEY\nfrom decision_diff_agent import DecisionDiffAgent, get_diff_agent"
c = c.replace(old, new)

# 2) Fix make_unified_decision - add diff agent
old_unified = """def make_unified_decision(groups):
  """对每组分别决策，返回包含所有片段决策的列表"""
  if not groups: return []
  results=[]
  for i,g in enumerate(groups):
    d=llm_decide(g) if use_llm else None
    if d is None: d=rule_decide(g)
    d["group_index"]=i+1
    d["group_soc"]=g["soc"]
    d["group_speed"]=g["speed"]
    d["group_traffic"]=g["traffic_condition"]
    d["group_driving"]=g["driving_condition"]
    results.append(d)
  return results"""

new_unified = """def make_unified_decision(groups):
  """对每组分别决策，每组均通过 Diff Agent 预估输出是否变化"""
  if not groups: return []
  results=[]
  for i,g in enumerate(groups):
    diff_pred = diff_agent.predict_change(g)
    d=llm_decide(g) if use_llm else None
    if d is None: d=rule_decide(g)
    d["group_index"]=i+1
    d["group_soc"]=g["soc"]
    d["group_speed"]=g["speed"]
    d["group_traffic"]=g["traffic_condition"]
    d["group_driving"]=g["driving_condition"]
    d["change_prediction"] = diff_pred
    diff_agent.record_decision(g, d)
    results.append(d)
  return results"""

c = c.replace(old_unified, new_unified)

with open(path, "w", encoding="utf-8") as f:
    f.write(c)
print("server.py fix applied OK")
