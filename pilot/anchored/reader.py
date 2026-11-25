"""Two model passes followed by mechanical evidence hydration and vote accounting."""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from decimal import Decimal
from pathlib import Path
from jsonschema import validate
from contract import STAGE1, STAGE2, BUCKETS
from prompts import S1,S2
from source import context,index_pdf

MODEL="gpt-4.1-mini-2025-04-14"
def encode(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)

def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()

def ask(model,stage,prompt,payload,schema):
    from openai import OpenAI
    with OpenAI(timeout=240,max_retries=0) as client:
        response=client.chat.completions.create(model=model,temperature=0,
            messages=[{"role":"system","content":prompt},{"role":"user","content":encode(payload)}],
            response_format={"type":"json_schema","json_schema":{"name":stage,"strict":True,"schema":schema}})
    result=response.choices[0]
    if result.finish_reason!="stop" or result.message.refusal:
        raise ValueError(stage+" incomplete: "+result.finish_reason)
    return json.loads(result.message.content),{"request_id":response.id,"model":response.model}

def cached_call(db,model,stage,prompt,payload,schema,offline,invoke):
    key=digest([model,stage,prompt,payload,schema])
    db.execute("BEGIN IMMEDIATE")
    try:
        row=db.execute("SELECT response,receipt FROM calls WHERE key=?",(key,)).fetchone()
        if row:
            data,receipt=map(json.loads,row)
        else:
            if offline:
                raise ValueError("Missing frozen response for "+stage)
            data,receipt=invoke(model,stage,prompt,payload,schema)
            validate(data,schema)
            db.execute("INSERT INTO calls VALUES(?,?,?)",(key,encode(data),encode(receipt)))
        validate(data,schema)
        db.commit()
        return data,dict(receipt,input_hash=key)
    except BaseException:
        db.rollback()
        raise

def open_cache(path):
    db=sqlite3.connect(path,timeout=600)
    db.execute("CREATE TABLE IF NOT EXISTS calls(key TEXT PRIMARY KEY,response TEXT NOT NULL,receipt TEXT NOT NULL)")
    return db

def numeric_values(text):
    s=unicodedata.normalize("NFKC",text).replace("−","-")
    s=re.sub(r"(?<=\d)\s*[-–—~至]\s*(?=\d)"," ",s)
    return {Decimal(m[0].replace(",","")) for m in re.finditer(r"(?<![\d.])[-+]?\d+(?:,\d{3})*(?:\.\d+)?(?![\d.])",s)}

def project(card):
    if card["bucket"]=="曲线形态" or card["bucket"]=="整体久期/组合":
        return None,None
    values={}
    for n in card["numbers"]:
        if n["unit"] in ("%","％") and n["window"]==card["horizon"] and n["value"] is not None:
            values.setdefault(n["role"],set()).add(n["value"])
    low,high=card["target_low"],card["target_high"]
    if low is None or high is None or low>high:
        return None,None
    supported=(low==high and low in values.get("预测单点目标",set())) or (
        low in values.get("预测区间下限",set()) and high in values.get("预测区间上限",set()))
    return (low,high) if supported else (None,None)

def finish(meta,index,candidates,review,visible):
    bypid={p["pid"]:p for p in index["paragraphs"]}
    known=set(candidates)
    used=set()
    handled=set()
    buckets=set()
    def refs(pids):
        if not pids or not set(pids)<=visible: raise ValueError("Missing or foreign evidence anchor")
        return [dict(pid=p,page=bypid[p]["page"],text=bypid[p]["text"]) for p in dict.fromkeys(pids)]
    def track(row,merged):
        ids=set(row["candidate_ids"])
        if not ids or not ids<=known: raise ValueError("Missing or unknown candidate id")
        if ids & handled or (not merged and ids & used): raise ValueError("Candidate has incompatible dispositions")
        (used if merged else handled).update(ids)
    cards=[]
    warnings=[]
    def accept(c):
        card=json.loads(encode(c))
        if sum(x["bucket"]==card["bucket"] for x in review["cards"])>1:
            raise ValueError("Duplicate report/bucket")
        card["evidence"]=refs(card["evidence_pids"])
        for part in [*card["drivers"],*card["scenarios"],*card["numbers"]]:
            refs(part["pids"])
            if not set(part["pids"])<=set(card["evidence_pids"]): raise ValueError("Card is missing subordinate anchor")
        for n in card["numbers"]:
            available=set()
            for p in context(index,n["pids"]): available.update(numeric_values(p["text"]))
            if n["value"] is not None and Decimal(str(n["value"])) not in available:
                warnings.append(dict(bucket=card["bucket"],reason="数字待核",submitted=n["value"],pids=n["pids"]))
                n.update(value=None,status="数字待核")
        low,high=project(card)
        if (low,high)!=(card["target_low"],card["target_high"]):
            warnings.append(dict(bucket=card["bucket"],reason="目标带待核",submitted=[card["target_low"],card["target_high"]]))
        card.update(meta,report_id=index["report_id"],report_category=review["report_category"],target_low=low,target_high=high)
        track(card,True)
        return card
    clues=[]
    intercepted=[]
    def intercept(row,reason):
        ids=[i for i in dict.fromkeys(row["candidate_ids"]) if i in known]
        pids=list(dict.fromkeys(p for i in ids for p in candidates[i]["pids"] if p in visible))
        intercepted.append(dict(reason=reason,candidate_ids=ids,bucket=row.get("bucket")))
        if pids:
            clues.append(dict(reason=reason,candidate_ids=ids,evidence_pids=pids,evidence=refs(pids)))
        handled.update(i for i in ids if i not in used)
    for c in review["cards"]:
        try: cards.append(accept(c))
        except ValueError as error: intercept(c,str(error))
    for clue in review["unmerged_clues"]:
        try:
            evidence=refs(clue["evidence_pids"])
            track(clue,False)
            clues.append(dict(clue,evidence=evidence))
        except ValueError as error: intercept(clue,str(error))
    rejected=[]
    for r in review["rejected"]:
        try:
            track(r,False)
            rejected.append(r)
        except ValueError as error: intercept(r,str(error))
    for cid in sorted(known-used-handled):
        intercept({"candidate_ids":[cid]},"Missing semantic disposition")
    return dict(**meta,report_id=index["report_id"],report_category=review["report_category"],
        cards=cards,unmerged_clues=clues,rejected=rejected,numeric_warnings=warnings,s3=intercepted)

def read_report(pdf,meta,db,model=MODEL,offline=False,invoke=ask):
    return read_index(index_pdf(pdf),meta,db,model,offline,invoke)

def read_index(idx,meta,db,model=MODEL,offline=False,invoke=ask):
    if not idx["paragraphs"]: raise ValueError("S0: no extractable text")
    first,r1=cached_call(db,model,"stage1",S1,dict(report=meta,paragraphs=idx["paragraphs"]),STAGE1,offline,invoke)
    valid={p["pid"] for p in idx["paragraphs"]}
    candidates={}
    invalid=[]
    for i,c in enumerate(first["candidates"],1):
        good=[p for p in c["pids"] if p in valid]
        if not good or len(good)!=len(c["pids"]):
            invalid.append(dict(candidate_ids=[f"C{i}"],reason="Invalid Stage 1 anchor",original=c))
        if good: candidates[f"C{i}"]=dict(c,pids=good)
    selected=context(idx,{pid for c in candidates.values() for pid in c["pids"]})
    headings=list(dict.fromkeys(p["heading"] for p in idx["paragraphs"] if p["heading"]))
    packet=dict(report=meta,headings=headings,candidates=candidates,context=selected)
    review,r2=cached_call(db,model,"stage2",S2,packet,STAGE2,offline,invoke)
    output=finish(meta,idx,candidates,review,{p["pid"] for p in selected})
    output["s3"].extend(invalid)
    return dict(output=output,s0=idx,s1=first,s2=review,receipts=[r1,r2])

def write_new(path,data):
    import tempfile
    path=Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as f:
        tmp=Path(f.name)
        f.write(encode(data).encode())
    try:
        os.link(tmp,path)
    finally: tmp.unlink(missing_ok=True)

def main():
    p=argparse.ArgumentParser()
    for k in ("pdf","metadata","cache","output"): p.add_argument(k,type=Path)
    p.add_argument("--offline",action="store_true")
    args=p.parse_args()
    if args.output.exists(): p.error("Output already exists")
    meta=json.loads(args.metadata.read_bytes())
    keys=("broker","series","report_date","title")
    if any(not isinstance(meta.get(k),str) or not meta[k] for k in keys): p.error("Missing report identity")
    db=open_cache(args.cache)
    try: result=read_report(args.pdf.read_bytes(),{k:meta[k] for k in keys},db,offline=args.offline)
    finally: db.close()
    write_new(args.output,result)

if __name__=="__main__": main()
