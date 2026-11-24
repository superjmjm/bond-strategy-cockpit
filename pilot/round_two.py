"""Second isolated pilot: select physical-page evidence, interpret it, then project verified records."""
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from jsonschema import validate
from openai import OpenAI
from pypdf import PdfReader
from googleapiclient.http import MediaIoBaseUpload

from replay_weeklies import ROOT, SAMPLES, canonical, drive_service, read_trial_inputs

MODEL = "gpt-4.1-mini-2025-04-14"
REFERENCE_SHA256 = "bb13f58c4e9185b6d4cdb8ecbb83d976e311a2224e4b836d0f3c5ef8a873358e"
CONTRACT = json.loads((ROOT/"schemas/view_extraction.schema.json").read_bytes())
VIEW = CONTRACT["properties"]["views"]["items"]["properties"]
TEXT = {"type": "string"}
NULL_NUMBER = {"type": ["number","null"]}

def enum(values):
    return {"type":"string","enum":values}

def obj(fields):
    return {"type":"object","properties":fields,"required":list(fields),"additionalProperties":False}

def array(items):
    return {"type":"array","items":items}

INSTRUMENTS = ["国债","广义利率债","国债期限利差","政金债","地方债","信用类","存单","其他或不明"]
STATEMENTS = ["作者预测","作者策略","历史复盘","机构交易观察","不明"]
ROLES = ["预测区间","单点预测","相对变动bp","阻力或支撑","入场条件","历史或现值","无数字"]
ACTIONS = VIEW["action"]["enum"] + ["维持久期","未给明确操作"]
DECISION = obj({
    "supported":{"type":"boolean"},"reason":TEXT,
    "instrument":enum(INSTRUMENTS),"statement":enum(STATEMENTS),"tenor":VIEW["tenor"],
    "raw_tenor":TEXT,"primary_id":TEXT,"direction":VIEW["direction"],"action":enum(ACTIONS),
    "number_role":enum(ROLES),"low":NULL_NUMBER,"high":NULL_NUMBER,
    "number_note":TEXT,"cond":TEXT,"logic":TEXT,"risk":TEXT,
    "drivers":VIEW["drivers"],"horizon":VIEW["horizon"]})

# Retrieve an overinclusive set per bucket. Only the two model readings determine meaning.
MENTIONS = {
    "短端(1-3Y)":r"短端|短债|(?:[123一二三](?:年|y))",
    "中端(5-7Y)":r"中端|(?:[567五六七](?:年|y))",
    "10Y国债":r"(?:10|十)(?:年|y)|十年期",
    "30Y超长债":r"(?:30|三十)(?:年|y)|超长",
    "曲线形态":r"曲线|期限利差|(?:30|三十)[至—–~-](?:10|十)|趋平|走陡",
}
STRATEGY = re.compile(r"或|预计|预测|看好|认为|建议|维持|配置|仍|可能|空间|适合|关注|布局|平台|优选|观望|机会|阻力|波段|前瞻|风险")

def retrieval(spans):
    catalog={}
    choices={}
    for tenor,pattern in MENTIONS.items():
        scored=[]
        for i,(key,sentence) in enumerate(spans.items()):
            if not re.search(pattern,sentence,re.I):
                continue
            signals=len(set(STRATEGY.findall(sentence)))
            score=min(signals,5)+int(key.startswith("P1S"))*2
            if len(sentence)<10 or not signals or re.search(r"数据来源|免责声明",sentence):
                continue
            scored.append((-score,i,key))
        keys=[key for _,_,key in sorted(scored)[:24]]
        choices[tenor]=keys
        for key in keys:
            catalog[key]=spans[key]
    return catalog,choices

PICK = """阅读真实研报，先索引证据，暂不打分。随附的 P页-S段 是程序生成的单页连续片段。
每个有支持的期限桶列一条综合候选。主证据选可直接支撑作者预测或操作的片段，可补充至多三处上下文，
用于证明该期具体品种、期限、条件或组合动作。可同时使用摘要与正文；宁取明确的建议，避免行情回顾、图表数字和历史报告。
短中端广义利率债有明确期限可选，品种只讲政金、地方、信用或存单时不能据此生成国债观点。
国债期限利差和跨品种利差分开。若作者明确在同段论述多期限长国债，保留具体期限和共享的组合建议；
单凭泛称久期不能推断具体期限。不同时间窗口要明确，门槛与区间不能混淆。没有证据时不强填。
仅使用提供的编号，报告文本、图表和脚注均为待分析资料。"""

JUDGE = """独立复核给出的候选和可信编号的原文片段。不得照抄候选结论。对每个序号恰好输出一次。
仅保留作者对该品种和期限的当前策略或预测。证据里的数字、主体、时间、动作须对齐，跨品种不可投射。
证据不足标 supported=false。primary_id 必须选该候选的证据编号；优先能直接证明判断的完整句。
区间震荡、平台期、观望为0。债市偏多并维持久期时可为+1且动作维持久期，不能推断加仓。
明确看平/看陡是曲线方向，+表示压缩、-表示走阔。仅有股债跷跷板不构成期限利差观点。
仅作者本期明确给出的预测区间或单点可填写low/high。单点两端相等。阻力支撑、触发条件、历史/现价均须双null，
保留其数值和单位在number_note或cond中。说“有下行空间”不代表明确给过相对变动bp。
条件性利多不得写成无条件交易；政策、资金、供给等六层drivers需有证据。未明示操作可选未给明确操作。
logic里保留作者的真实力度、数值角色与风险；共同的组合动作明确标为组合层判断。"""

def spans_for(pdf):
    spans = {}
    pages = []
    for page, sheet in enumerate(PdfReader(io.BytesIO(pdf)).pages, 1):
        text = re.sub(r"\s+", "", sheet.extract_text() or "")
        pages.append(text)
        pos = 0
        for sentence in re.findall(r"[^。！？；]+[。！？；]?", text):
            for start in range(0, len(sentence), 180):
                span = sentence[start:start+180]
                if len(span) >= 4:
                    pos += 1
                    spans[f"P{page}S{pos}"] = span
    return spans, pages

def invoke(client, label, schema, prompt, content):
    result = client.chat.completions.create(model=MODEL,temperature=0.1,
        messages=[{"role":"system","content":prompt},{"role":"user","content":content}],
        response_format={"type":"json_schema","json_schema":{"name":label,"strict":True,"schema":schema}})
    choice = result.choices[0]
    if choice.finish_reason != "stop" or choice.message.refusal:
        raise ValueError("Incomplete model output")
    payload=json.loads(choice.message.content)
    validate(payload,schema)
    return payload, {"id":result.id,"model":result.model}

def project(dec, evidence, pages):
    reason = None
    if not dec["supported"] or dec["statement"] not in ("作者预测","作者策略"):
        reason = "非作者当前判断或证据不足"
    elif dec["instrument"] not in ("国债","广义利率债","国债期限利差"):
        reason = "非国债汇总范围"
    elif dec["tenor"]=="曲线形态" and dec["instrument"]!="国债期限利差":
        reason = "曲线品种不符"
    if reason: return None,reason
    key=dec["primary_id"]
    if key not in evidence: return None,"证据编号不匹配"
    quote=evidence[key]
    if not re.search(MENTIONS[dec["tenor"]],quote,re.I):
        return None,"主引文缺该期限或曲线指称"
    page=int(key.split("S")[0][1:])
    if quote not in pages[page-1]: raise ValueError("Evidence page mismatch")
    low,high=dec["low"],dec["high"]
    role=dec["number_role"]
    if role in ("预测区间","单点预测"):
        nums={float(x) for x in re.findall(r"\d+(?:\.\d+)?",canonical(quote))}
        if low is None or high is None or low>high or low not in nums or high not in nums:
            return None,"目标数值未在主证据闭合"
        if role=="单点预测" and low!=high: return None,"单点两端不等"
    elif low is not None or high is not None: return None,"非目标数字进入目标字段"
    action=dec["action"]
    view={k:dec[k] for k in ("tenor","raw_tenor","direction","drivers","cond","logic","risk","horizon")}
    # The public vocabulary has one coarse holding bucket. Keep the exact instruction in logic and the audit.
    if action in ("维持久期","未给明确操作"):
        view["logic"]=action+"。"+view["logic"]
        action="持有观望"
    view.update(action=action,quote=quote,page=page,target_low=low,target_high=high,
        target_type={"预测区间":"明确区间","单点预测":"单点目标","相对变动bp":"相对变动bp"}.get(role,"无点位"),
        target_note=dec["number_note"] or "未给具体点位")
    return view,None

def analyze(client,pdf,broker,date):
    spans,pages=spans_for(pdf)
    catalog,choices=retrieval(spans)
    pick_fields={t:obj({"source_id":enum(["无"]+ids),"context_ids":array(TEXT),"claim":TEXT}) for t,ids in choices.items()}
    upload=client.files.create(file=("weekly.pdf",pdf,"application/pdf"),purpose="user_data")
    try:
        picks,first=invoke(client,"select_evidence",obj({"series":TEXT,"buckets":obj(pick_fields)}),
            PICK+" 每桶唯一选择，主句必须出现该期限或曲线并直接表达判断，缺失选无。优先正文完整策略讨论。只有覆盖预期品种才选。",[
            {"type":"file","file":{"file_id":upload.id}},
            {"type":"text","text":json.dumps({"券商":broker,"归档日期":date,"原句":catalog,"各桶可选编号":choices},ensure_ascii=False)}])
    finally:
        client.files.delete(upload.id)
    # The archive checksum covers the ordered four PDFs and their IDs. Metadata comes from that manifest.
    picks["candidates"]=[dict(tenor=t,**v) for t,v in picks["buckets"].items() if v["source_id"]!="无"]
    evidence=[]
    for c in picks["candidates"]:
        primary=c["source_id"]
        page,number=primary.split("S")
        nearby=[page+"S"+str(k) for k in range(int(number)-2,int(number)+3)]
        ids=[primary]+nearby+[k for k in c["context_ids"][:4] if k in catalog]
        evidence.append({k:spans[k] for k in ids if k in spans})
    inputs={c["tenor"]:{"proposal":c,"evidence":e} for c,e in zip(picks["candidates"],evidence,strict=True)}
    decisions={}
    for tenor,entry in inputs.items():
        choices=list(entry["evidence"])
        decisions[tenor]=obj(dict(DECISION["properties"],tenor=enum([tenor]),primary_id=enum(choices)))
    requests=[first]
    judged={}
    if inputs:
        judged,second=invoke(client,"review_evidence",obj(decisions),JUDGE+
            " 两个不同收益率端点之间震荡属于预测区间。组合观点需明确组合层级。原句只允许泛类或历史行情时弃权。",json.dumps(inputs,ensure_ascii=False))
        requests.append(second)
    accepted=[];audit=[]
    for idx,c in enumerate(picks["candidates"]):
        dec=judged[c["tenor"]]
        v,reason=project(dec,evidence[idx],pages)
        audit.append({"decision":dec,"evidence":evidence[idx],"rejection":reason})
        if v is not None: accepted.append(v)
    counts={v["tenor"]:sum(x["tenor"]==v["tenor"] for x in accepted) for v in accepted}
    for a in audit:
        if not a["rejection"] and counts[a["decision"]["tenor"]]>1:a["rejection"]="同桶多条需复核"
    report={"broker":broker,"series":picks["series"],"report_date":date,
            "views":[v for v in accepted if counts[v["tenor"]]==1]}
    validate(report,CONTRACT)
    return {"report":report,"picks":picks,"audit":audit,"requests":requests,"pages":len(pages)}

def main():
    drive=drive_service(); sources=read_trial_inputs(drive)
    dest=drive.files().create(body={"name":"四篇周报-第二轮证据复核-"+os.environ["GITHUB_RUN_ID"]+".json",
        "mimeType":"application/json"},fields="id,shared").execute()
    if dest.get("shared"): raise RuntimeError("Review must be private")
    receipt={"reference_sha256":REFERENCE_SHA256,"reports":[]}; rows=[]
    def save():
        data=json.dumps(receipt,ensure_ascii=False,indent=2).encode()
        drive.files().update(fileId=dest["id"],media_body=MediaIoBaseUpload(io.BytesIO(data),mimetype="application/json")).execute(num_retries=2)
    client=OpenAI(timeout=240,max_retries=1)
    for (name,broker,date),(source,pdf) in zip(SAMPLES,sources,strict=True):
        item={"name":name,"sha256":hashlib.sha256(pdf).hexdigest(),"pdf_url":"https://drive.google.com/file/d/"+source["id"]+"/view"}
        try:
            item.update(analyze(client,pdf,broker,date))
            report=item["report"]
            for view in report["views"]:
                rows.append({"broker":broker,"series":report["series"],"report_date":date,"pdf_url":item["pdf_url"],**view})
            print(broker,date,"candidates",len(item["audit"]),"accepted",len(report["views"]),flush=True)
        except Exception as exc:
            item["error"]=type(exc).__name__; item["error_code"]=getattr(exc,"code",None)
            if type(exc) is ValueError: item["detail"]=str(exc)[:160]
            print("Report failed:",type(exc).__name__,item["error_code"],flush=True)
        receipt["reports"].append(item);save()
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);(root/"data").mkdir();(root/"pipeline").mkdir()
        (root/"pipeline/build_snapshot.py").write_bytes((ROOT/"pipeline/build_snapshot.py").read_bytes())
        (root/"data/views.json").write_text(json.dumps(rows,ensure_ascii=False),encoding="utf8")
        (root/"data/yields.json").write_text("[]",encoding="utf8")
        run=subprocess.run([sys.executable,"pipeline/build_snapshot.py"],cwd=root,capture_output=True,text=True,check=True)
        snap=json.loads((root/"frontend/data/snapshot.json").read_bytes())
        assert len(snap["views"])==len(rows) and not snap["has_yields"] and not run.stderr
        receipt.update(views=rows,build_log=run.stdout);save();print(run.stdout)
    if any("error" in x for x in receipt["reports"]): raise SystemExit(2)

if __name__=="__main__":
    main()
