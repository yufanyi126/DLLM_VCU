# -*- coding: utf-8 -*-
# VCU Agent Dashboard Server
import sys,os,json,threading
from http.server import HTTPServer,BaseHTTPRequestHandler
from datetime import datetime
from urllib.parse import urlparse
_d=os.path.dirname(os.path.abspath(__file__))
_r=os.path.dirname(_d)
if _r not in sys.path: sys.path.insert(0,_r)
if _d not in sys.path: sys.path.insert(0,_d)
from llm_stdlib import llm_decide,rule_decide,API_KEY
from decision_diff_agent import DecisionDiffAgent, get_diff_agent
from db import init_db, save_report, load_all_reports, clear_all_reports
use_llm = not ("your-deepseek" in API_KEY)
diff_agent = get_diff_agent(use_llm=False)
decision_history = []
decision_queue = []
state_groups = []
cycle_counter = [0]
test_reports = []
init_db()
try:
    clear_all_reports(); test_reports = load_all_reports()
except Exception:
    test_reports = []
lock = threading.Lock()

def make_decision(sd):
  global cycle_counter; cycle_counter[0]+=1
  diff_pred = diff_agent.predict_change(sd)
  d=llm_decide(sd) if use_llm else None
  if d is None: d=rule_decide(sd)
  diff_agent.record_decision(sd, d)
  return {"cycle":cycle_counter[0],"timestamp":datetime.now().strftime("%H:%M:%S"),**sd,**d,"change_prediction":diff_pred}

def make_unified_decision(groups):
  """Unified decision with diff agent per group"""
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
    d["change_prediction"]=diff_pred
    diff_agent.record_decision(g, d)
    results.append(d)
  return results

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>VCU Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;background:#0d1117;color:#c9d1d9}
.header{background:linear-gradient(135deg,#0d1117,#161b22,#1f2937);color:#e6edf3;padding:18px 30px;display:flex;align-items:center;gap:15px;flex-wrap:wrap;border-bottom:1px solid #30363d}
.header h1{font-size:22px;font-weight:600}
.badge{background:rgba(255,255,255,0.08);padding:4px 12px;border-radius:12px;font-size:12px}
.lang-btn{background:rgba(255,255,255,0.08);border:1px solid #30363d;color:#e6edf3;padding:6px 14px;border-radius:8px;cursor:pointer;font-size:12px}
.lang-btn:hover{background:rgba(255,255,255,0.15)}
.container{display:flex;gap:20px;padding:20px;max-width:1600px;margin:0 auto}
.panel{background:#161b22;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.3);overflow:hidden;border:1px solid #30363d}
.panel-title{background:#1c2333;padding:12px 18px;font-size:14px;font-weight:600;border-bottom:1px solid #30363d;color:#e6edf3}
.panel-body{padding:16px}
.left-panel{width:380px;flex-shrink:0}
.right-panel{flex:1;display:flex;flex-direction:column;gap:20px}
.form-group{margin-bottom:12px}
.form-group label{display:block;font-size:12px;font-weight:500;color:#8b949e;margin-bottom:4px}
.form-group input,.form-group select{width:100%;padding:8px 10px;border:1px solid #30363d;border-radius:6px;font-size:13px;background:#0d1117;color:#c9d1d9}
.form-group input:focus,.form-group select:focus{outline:none;border-color:#58a6ff}
.btn{padding:10px 20px;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;transition:opacity 0.2s}
.btn:hover{opacity:0.85}
.btn-primary{background:#1f6feb;color:white}
.btn-success{background:#238636;color:white}
.btn-warning{background:#d29922;color:white}
.btn-secondary{background:#21262d;color:#c9d1d9;border:1px solid #30363d}
.btn-danger{background:#da3633;color:white}
.btn-sm{padding:6px 14px;font-size:12px}
.btn-group{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
.dcards{display:flex;gap:15px;margin:10px 0}
.dcard{flex:1;padding:18px;border-radius:10px;text-align:center;background:#0d1117;border:1px solid #30363d}
.dcard .lbl{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:1px}
.dcard .val{font-size:28px;font-weight:700;margin:5px 0;color:#e6edf3}
.dcard .dsc{font-size:11px;color:#8b949e}
.rbox{background:#1c2333;padding:12px 16px;border-radius:8px;font-size:13px;color:#d29922;border-left:3px solid #d29922;margin:10px 0}
.section-title{font-size:13px;font-weight:600;color:#8b949e;text-transform:uppercase;letter-spacing:1px;margin:16px 0 8px}
.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.status-dot.green{background:#3fb950}
.status-dot.yellow{background:#d29922}
.diff-badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;margin-left:6px}
.diff-badge.change{background:#da3633;color:white}
.diff-badge.same{background:#238636;color:white}
</style></head><body>
<div class=header><h1>VCU Agent Dashboard</h1><span class=badge id=statusBadge></span>
<span class=badge id=langBadge></span>
<button class=lang-btn onclick=toggleLang() id=langBtn>English</button>
</div>
<div class=container>
<div class="left-panel panel"><div class=panel-title id=panelTitle>Control Panel</div><div class=panel-body>
<div id=inputForm>
<div class=form-group><label data-i18n=route>Route Description</label><input id=route placeholder="City Center to Airport" value="City Center to Airport"></div>
<div class=section-title data-i18n=gaode>Gaode API (Traffic)</div>
<div class=form-group><label data-i18n=traffic>Traffic Condition</label><select id=traffic><option value=smooth selected>--</option><option value=smooth>Smooth</option><option value=slow>Slow</option><option value=congested>Congested</option><option value=severe>Severe</option><option value=unknown>Unknown</option></select></div>
<div class=section-title data-i18n=rms>RMS (Driving Condition)</div>
<div class=form-group><label data-i18n=driving>Driving Condition</label><select id=driving><option value=urban_smooth selected>--</option><option value=highway_cruise>Highway Cruise</option><option value=elevated_cruise>Elevated Cruise</option><option value=urban_congested>Urban Congested</option><option value=urban_smooth>Urban Smooth</option><option value=suburban>Suburban</option><option value=mountain>Mountain</option></select></div>
<div class=form-group><label data-i18n=driver_style>Driver Style</label><select id=driver_style><option value=normal selected>--</option><option value=conservative>Conservative</option><option value=normal>Normal</option><option value=aggressive>Aggressive</option></select></div>
<div class=section-title data-i18n=vcu>VCU Gateway</div>
<div class=form-group><label data-i18n=soc>SOC (0.0-1.0)</label><div><input type=range id=soc min=0 max=1 step=0.01 value=0.85 oninput=sv(soc,sv_soc)><span class=range-val id=sv_soc>0.85</span></div></div>
<div class=form-group><label data-i18n=speed>Speed (km/h)</label><div><input type=range id=speed min=0 max=150 step=1 value=60 oninput=sv(speed,sv_speed)><span class=range-val id=sv_speed>60</span></div></div>
<div class=form-group><label data-i18n=gear>Gear</label><select id=gear><option value=P>P</option><option value=N>N</option><option value=D selected>D</option><option value=R>R</option><option value=B>B</option></select></div>
<div class=form-group><label data-i18n=throttle>Throttle Angle (%)</label><div><input type=range id=throttle min=0 max=100 step=1 value=30 oninput=sv(throttle,sv_throttle)><span class=range-val id=sv_throttle>30</span></div></div>
<div class=form-group><label data-i18n=battery_temp>Battery Temp (C)</label><div><input type=range id=battery_temp min=-10 max=60 step=1 value=25 oninput=sv(battery_temp,sv_battery_temp)><span class=range-val id=sv_battery_temp>25</span></div></div>
<div class=section-title data-i18n=gps>GPS</div>
<div class=row><div class=form-group><label data-i18n=lat>Latitude</label><input id=lat value=39.9042></div>
<div class=form-group><label data-i18n=lng>Longitude</label><input id=lng value=116.4074></div></div>
<div class=form-group><label data-i18n=ts>Timestamp</label><input id=ts placeholder="Auto"></div>
</div>
<div class=btn-group>
<button class="btn btn-primary btn-sm" onclick=doDecide() data-i18n=decide>Decide</button>
<button class="btn btn-success btn-sm" onclick=addToQueue() data-i18n=add_q>Add to Queue</button>
<button class="btn btn-warning btn-sm" onclick=runQueue() data-i18n=run_q>Run Queue</button>
<button class="btn btn-secondary btn-sm" onclick=clearQueue() data-i18n=clear_q>Clear Queue</button>
<button class="btn btn-secondary btn-sm" onclick=addGroup() data-i18n=add_g>Add Group</button>
<button class="btn btn-success btn-sm" onclick=submitGroups() data-i18n=submit_g>Submit Groups</button>
<button class="btn btn-danger btn-sm" onclick=clearGroups() data-i18n=clear_g>Clear Groups</button>
<button class="btn btn-danger btn-sm" onclick=clearHistory() data-i18n=clear_h>Clear History</button>
</div>
</div></div>
<div class=right-panel>
<div class=panel><div class=panel-title data-i18n=live_dec>Live Decision</div><div class=panel-body><div id=liveDecision></div></div></div>
<div class=panel><div class=panel-title data-i18n=diff_pred>Diff Prediction</div><div class=panel-body><div id=diffPrediction></div></div></div>
<div class=panel><div class=panel-title data-i18n=history>Decision History</div><div class=panel-body><canvas id=historyChart height=200></canvas><div id=historyList style="max-height:300px;overflow-y:auto;margin-top:10px"></div></div></div>
<div class=panel><div class=panel-title data-i18n=queue>Queue (<span id=queueCount>0</span>)</div><div class=panel-body><div id=queueList></div></div></div>
<div class=panel><div class=panel-title data-i18n=groups>Groups (<span id=groupCount>0</span>/20)</div><div class=panel-body><div id=groupList></div></div></div>
<div class=panel><div class=panel-title data-i18n=reports>Reports</div><div class=panel-body><div id=reportList></div></div></div>
</div></div>
<script>
var L={zh:{route:"Route",gaode:"Gaode",traffic:"Traffic",rms:"RMS",driving:"Driving",driver_style:"Driver Style",vcu:"VCU",soc:"SOC",speed:"Speed",gear:"Gear",throttle:"Throttle",battery_temp:"Battery Temp",gps:"GPS",lat:"Latitude",lng:"Longitude",ts:"Timestamp",decide:"Decide",add_q:"Add to Queue",run_q:"Run Queue",clear_q:"Clear Queue",add_g:"Add Group",submit_g:"Submit Groups",clear_g:"Clear Groups",clear_h:"Clear History",live_dec:"Live Decision",diff_pred:"Diff Prediction",history:"History",queue:"Queue",groups:"Groups",reports:"Reports",panelTitle:"Control Panel"},en:{route:"Route Description",gaode:"Gaode API (Traffic)",traffic:"Traffic Condition",rms:"RMS (Driving Condition)",driving:"Driving Condition",driver_style:"Driver Style",vcu:"VCU Gateway",soc:"SOC (0.0-1.0)",speed:"Speed (km/h)",gear:"Gear",throttle:"Throttle Angle (%)",battery_temp:"Battery Temp (C)",gps:"GPS",lat:"Latitude",lng:"Longitude",ts:"Timestamp",decide:"Decide",add_q:"Add to Queue",run_q:"Run Queue",clear_q:"Clear Queue",add_g:"Add Group",submit_g:"Submit Groups",clear_g:"Clear Groups",clear_h:"Clear History",live_dec:"Live Decision",diff_pred:"Diff Prediction",history:"Decision History",queue:"Queue",groups:"Groups",reports:"Reports",panelTitle:"Control Panel"}}
var lang="zh";function toggleLang(){lang=lang==="zh"?"en":"zh";applyLang();}
function applyLang(){var m=L[lang];document.querySelectorAll("[data-i18n]").forEach(function(el){var k=el.getAttribute("data-i18n");if(m[k]){if(el.tagName==="LABEL"||el.tagName==="SPAN"||el.tagName==="DIV"||el.tagName==="BUTTON")el.textContent=m[k];else if(el.tagName==="TITLE")el.textContent=m[k];else el.textContent=m[k]}});document.getElementById("langBtn").textContent=lang==="zh"?"English":"Chinese";document.getElementById("langBadge").textContent=lang==="zh"?"CN":"EN";}
function sv(src,tgt){document.getElementById(tgt).textContent=document.getElementById(src).value}
function gv(){return{route_description:document.getElementById("route").value,traffic_condition:document.getElementById("traffic").value,driving_condition:document.getElementById("driving").value,driver_style:document.getElementById("driver_style").value,soc:parseFloat(document.getElementById("soc").value),speed:parseFloat(document.getElementById("speed").value),gear:document.getElementById("gear").value,throttle_angle:parseFloat(document.getElementById("throttle").value),battery_temp:parseFloat(document.getElementById("battery_temp").value),latitude:parseFloat(document.getElementById("lat").value),longitude:parseFloat(document.getElementById("lng").value),timestamp:document.getElementById("ts").value||new Date().toISOString()}}
function showStatus(msg,err){var sb=document.getElementById("statusBadge");sb.innerHTML="<span class=status-dot "+(err?"yellow":"green")+"></span>"+msg;}
async function api(path,data){var r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:data?JSON.stringify(data):"{}"});return await r.json()}
var chart=null;function initChart(){chart=new Chart(document.getElementById("historyChart"),{type:"line",data:{labels:[],datasets:[{label:"SOC",data:[],borderColor:"#58a6ff",fill:false,yAxisID:"y"},{label:"Speed",data:[],borderColor:"#3fb950",fill:false,yAxisID:"y1"}]},options:{responsive:true,interaction:{mode:"index",intersect:false},plugins:{legend:{labels:{color:"#8b949e"}}},scales:{x:{ticks:{color:"#8b949e"}},y:{type:"linear",display:true,position:"left",min:0,max:1,ticks:{color:"#58a6ff"}},y1:{type:"linear",display:true,position:"right",min:0,max:150,grid:{drawOnChartArea:false},ticks:{color:"#3fb950"}}}}})}
function showDecision(d,target){var em=d.energy_mode||"-",dm=d.driving_mode||"-",r=d.reasoning||"";target=target||"liveDecision";var cp=d.change_prediction||{};var cpHTML="";if(cp.prediction){var pc=cp.prediction==="CHANGE"?"CHANGE":"SAME";var cls=pc==="CHANGE"?"change":"same";cpHTML="<div style=margin-top:8px;padding:8px;background:#1c2333;border-radius:6px;font-size:12px><b>Diff Prediction:</b> <span class=diff-badge "+cls+">"+pc+"</span><br>Confidence: "+(cp.confidence||"-")+"<br>Fields: "+(cp.changed_fields&&cp.changed_fields.length?cp.changed_fields.join(", "):"-")+"<br>Reasoning: "+(cp.reasoning||"")+"</div>"}
  document.getElementById(target).innerHTML="<div class=dcards><div class=dcard><div class=lbl>Energy</div><div class=val>"+em+"</div><div class=dsc>signal: "+(d.energy_signal||"-")+"</div></div><div class=dcard><div class=lbl>Drive</div><div class=val>"+dm+"</div><div class=dsc>signal: "+(d.drive_signal||"-")+"</div></div><div class=dcard><div class=lbl>Reason</div><div class=val style=font-size:14px>"+r.substring(0,60)+"</div></div></div>"+cpHTML}
function addHistoryRow(d){var el=document.getElementById("historyList");var entry=document.createElement("div");entry.style.cssText="padding:6px 10px;border-bottom:1px solid #30363d;font-size:12px;display:flex;justify-content:space-between";var cp=d.change_prediction||{};var pc=cp.prediction==="CHANGE"?"C":"S";entry.innerHTML="<span>#"+(d.cycle||d.group_index||"?")+" SOC:"+(d.soc||d.group_soc||"-")+" Spd:"+(d.speed||d.group_speed||"-")+"</span><span>"+(d.energy_mode||"-")+" / "+(d.driving_mode||"-")+" <span class=diff-badge "+(pc==="C"?"change":"same")+">"+pc+"</span></span>";el.insertBefore(entry,el.firstChild)}
function updateQueue(q){document.getElementById("queueCount").textContent=q.length;var el=document.getElementById("queueList");el.innerHTML="";q.forEach(function(v,i){var d=document.createElement("div");d.style.cssText="padding:4px 8px;font-size:12px;border-bottom:1px solid #30363d";d.textContent="#"+i+" SOC:"+v.soc+" Spd:"+v.speed;el.appendChild(d)})}
function updateGroups(g){document.getElementById("groupCount").textContent=g.length;var el=document.getElementById("groupList");el.innerHTML="";g.forEach(function(v,i){var d=document.createElement("div");d.style.cssText="padding:4px 8px;font-size:12px;border-bottom:1px solid #30363d;display:flex;justify-content:space-between";d.innerHTML="<span>#"+(i+1)+" SOC:"+v.soc+" Spd:"+v.speed+" "+v.driving_condition+"</span><button class=btn btn-sm btn-danger onclick=removeGroup("+i+") style=padding:2px 8px;font-size:10px>X</button>";el.appendChild(d)})}
function updateReports(rs){var el=document.getElementById("reportList");el.innerHTML="";if(!rs||rs.length===0){el.innerHTML="<div style=font-size:12px;color:#8b949e>No reports</div>";return}rs.slice().reverse().forEach(function(r){var d=document.createElement("div");d.style.cssText="padding:6px 10px;border-bottom:1px solid #30363d;font-size:12px";d.innerHTML="<b>#"+(r.id||"")+"</b> "+(r.num_groups||r.sequences?.length||0)+" groups "+(r.timestamp||"");d.onclick=function(){loadReport(r.id)};el.appendChild(d)})}
var cachedReports={};function loadReport(id){var r=cachedReports[id];if(!r)return;var html="<div style=font-size:12px>";(r.decisions||[]).forEach(function(d,i){var cp=d.change_prediction||{};var pc=cp.prediction==="CHANGE"?"C":"S";html+="<div style=padding:6px;margin:4px 0;background:#0d1117;border-radius:4px><b>#"+(i+1)+"</b> SOC:"+d.group_soc+" Spd:"+d.group_speed+" -> "+d.energy_mode+"/"+d.driving_mode+" <span class=diff-badge "+(pc==="C"?"change":"same")+">"+pc+"</span></div>"});html+="</div>";document.getElementById("liveDecision").innerHTML=html}
async function doDecide(){var d=gv();var r=await api("/api/decide",d);if(r.decision){showDecision(r.decision);addHistoryRow(r.decision);if(chart){chart.data.labels.push(r.decision.cycle);chart.data.datasets[0].data.push(r.decision.soc);chart.data.datasets[1].data.push(r.decision.speed);if(chart.data.labels.length>50){chart.data.labels.shift();chart.data.datasets[0].data.shift();chart.data.datasets[1].data.shift()}chart.update()}showStatus("OK")}}
async function addToQueue(){var d=gv();var r=await api("/api/queue/add",d);updateQueue(r.queue)}
async function runQueue(){var r=await api("/api/queue/run",{});(r.queue_decisions||[]).forEach(function(d){showDecision(d);addHistoryRow(d)});updateQueue(r.queue);showStatus("Queue run OK")}
async function clearQueue(){var r=await api("/api/queue/clear",{});updateQueue(r.queue)}
async function addGroup(){var d=gv();var r=await api("/api/groups/add",d);updateGroups(r.groups)}
async function removeGroup(idx){var r=await api("/api/groups/remove",{index:idx});updateGroups(r.groups)}
async function clearGroups(){var r=await api("/api/groups/clear",{});updateGroups(r.groups)}
async function submitGroups(){var r=await api("/api/groups/submit",{});if(r.groups_decisions){r.groups_decisions.forEach(function(d){showDecision(d,"liveDecision");addHistoryRow(d)})}updateGroups(r.groups);updateReports(r.reports);if(chart){r.groups_decisions.forEach(function(d){chart.data.labels.push("G"+d.group_index);chart.data.datasets[0].data.push(d.group_soc);chart.data.datasets[1].data.push(d.group_speed)});if(chart.data.labels.length>50){chart.data.labels=chart.data.labels.slice(-50);chart.data.datasets[0].data=chart.data.datasets[0].data.slice(-50);chart.data.datasets[1].data=chart.data.datasets[1].data.slice(-50)}chart.update()}showStatus("Groups submitted")}
async function clearHistory(){var r=await api("/api/clear",{});document.getElementById("historyList").innerHTML="";if(chart){chart.data.labels=[];chart.data.datasets[0].data=[];chart.data.datasets[1].data=[];chart.update()}showStatus("Cleared")}
async function refreshUI(){var r=await(await fetch("/api/status")).json();updateQueue(r.queue);updateGroups(r.groups);updateReports(r.reports);(r.history||[]).forEach(function(d){addHistoryRow(d)});if(r.history&&r.history.length&&chart){r.history.forEach(function(d){chart.data.labels.push(d.cycle||d.group_index);chart.data.datasets[0].data.push(d.soc||d.group_soc);chart.data.datasets[1].data.push(d.speed||d.group_speed)});chart.update()}}
async function init(){initChart();await refreshUI();showStatus("Ready",false)}
init();
</script></body></html>"""

class VHandler(BaseHTTPRequestHandler):
   def _json(self,d,c=200):
    self.send_response(c);self.send_header("Content-Type","application/json; charset=utf-8")
    self.send_header("Access-Control-Allow-Origin","*");self.send_header("Cache-Control","no-store");self.end_headers()
    self.wfile.write(json.dumps(d,ensure_ascii=False).encode("utf-8"))
   def _html(self,c):
    self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8");self.send_header("Cache-Control","no-cache");self.end_headers()
    self.wfile.write(c.encode("utf-8"))
   def do_OPTIONS(self):
    self.send_response(200)
    for h in ["Origin","Methods","Headers"]: self.send_header(f"Access-Control-Allow-{h}","*")
    self.end_headers()
   def do_GET(self):
    p=urlparse(self.path).path
    if p in ("/","/index.html"): self._html(HTML)
    elif p=="/api/status":
     with lock: self._json({"llm":"DeepSeek" if use_llm else "Rule","hc":len(decision_history),"qc":len(decision_queue),"gc":len(state_groups),"rc":len(test_reports),"history":decision_history[-50:],"queue":decision_queue,"groups":state_groups,"reports":test_reports,"last_report":test_reports[-1] if test_reports else None})
    elif p=="/api/reports":
     with lock: self._json({"reports":test_reports})
    else: self._json({"error":"not found"},404)
   def do_POST(self):
    p=urlparse(self.path).path;cl=int(self.headers.get("Content-Length",0))or None;b=self.rfile.read(cl)if cl else b"{}";d=json.loads(b)if cl else {}
    with lock:
     if p=="/api/decide":
      rec=make_decision(d);decision_history.append(rec);self._json({"decision":rec,"history":decision_history[-50:],"queue":decision_queue,"groups":state_groups})
     elif p=="/api/queue/add": decision_queue.append(d);self._json({"queue":decision_queue,"groups":state_groups})
     elif p=="/api/queue/run":
      qresults=[]
      for i in decision_queue: r=make_decision(i); decision_history.append(r); qresults.append(r)
      decision_queue.clear();self._json({"decision":qresults[-1]if qresults else None,"queue_decisions":qresults,"history":decision_history[-50:],"queue":[],"groups":state_groups})
     elif p=="/api/queue/clear": decision_queue.clear();self._json({"queue":[],"groups":state_groups})
     elif p=="/api/groups/add":
      if len(state_groups)>=20: self._json({"error":"Max 20 groups","groups":state_groups});return
      state_groups.append(d);self._json({"groups":state_groups})
     elif p=="/api/groups/remove":
      idx=d.get("index",0)
      if 0<=idx<len(state_groups): state_groups.pop(idx)
      self._json({"groups":state_groups})
     elif p=="/api/groups/clear": state_groups.clear();self._json({"groups":[]})
     elif p=="/api/groups/submit":
      snapshots=list(state_groups)
      if not state_groups: self._json({"error":"No groups","history":decision_history[-50:],"queue":decision_queue,"groups":[]});return
      gd=make_unified_decision(state_groups)
      if gd:
        for r in gd:
          cycle_counter[0]+=1
          r["cycle"]=cycle_counter[0]
          r["timestamp"]=datetime.now().strftime("%H:%M:%S")
          decision_history.append(r)
        new_id=save_report(len(test_reports)+1,len(snapshots),gd,snapshots)
        test_reports.append({"id":new_id,"num_groups":len(snapshots),"sequences":snapshots,"decisions":gd,"timestamp":datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
      state_groups.clear()
      self._json({"groups_decisions":gd if gd else [],"decision":gd[-1]if gd else None,"history":decision_history[-50:],"queue":decision_queue,"groups":[],"reports":test_reports})
     elif p=="/api/clear": decision_history.clear();cycle_counter[0]=0;self._json({"history":[],"queue":decision_queue,"groups":state_groups})
     elif p=="/api/reports/clear": clear_all_reports();test_reports.clear();self._json({"reports":[]})
   def log_message(self,*a):pass

def run(port=8080):
   server=HTTPServer(("127.0.0.1",port),VHandler)
   print("\n"+"="*60+"\n  VCU Agent Dashboard\n  LLM: "+("DeepSeek"if use_llm else"Rule-based")+f"\n  URL: http://127.0.0.1:{port}\n  Groups: up to 20\n"+"="*60+"\n")
   try: server.serve_forever()
   except KeyboardInterrupt: print("\nStopped.");server.server_close()

if __name__=="__main__": run()