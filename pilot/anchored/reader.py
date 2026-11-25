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
from contract import STAGE1, STAGE2, SLOTS
from prompts import S1,S2
from source import context,index_pdf

MODEL = "gpt-6.2-sol"
REASONING_EFFORT = "medium"
def encode(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)

def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()

def ask(model,stage,prompt,payload,schema):
    from openai import OpenAI
    with OpenAI(timeout=240,max_retries=0) as client:
        response=client.chat.completions.create(model=model,reasoning_effort=REASONING_EFFORT,
            messages=[{"role":"system","content":prompt},{"role":"user","content":encode(payload)}],
            response_format={"type":"json_schema","json_schema":{"name":stage,"strict":True,"schema":schema}})
    result=response.choices[0]
    if result.finish_reason!="stop" or result.message.refusal:
        raise ValueError(stage+" incomplete: "+result.finish_reason)
    return json.loads(result.message.content),{"request_id":response.id,"model":response.model,
            "requested_model":model,"reasoning_effort":REASONING_EFFORT,
            "usage":response.usage.model_dump() if response.usage else None}

def cached_call(db,model,stage,prompt,payload,schema,offline,invoke):
    key=digest([model,REASONING_EFFORT,stage,prompt,payload,schema])
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

# Parse whole number tokens and shared-unit ranges. Never borrow a unit from a
# different observation (e.g. 4/8 indicators next to a yield or turnover rate).
NUMBER_TOKEN = r"[+\-]?\d+(?:\.\d+)?"
MEASURE = re.compile(
    rf"(?<![\d./+\-])({NUMBER_TOKEN})(?:\s*(?:[-~～–—至到])\s*({NUMBER_TOKEN}))?"
    r"\s*(%|bp(?![a-z])|基点)", re.IGNORECASE)


def targets(card, paragraphs):
    low, high, unit = (card[key] for key in ("target_low","target_high","target_unit"))
    if low is None and high is None and unit is None:
        return True
    limits = (-50,150,"bp") if card["bucket"] == "曲线形态" else (0.5,4.5,"%")
    if (card["bucket"] == "整体久期/组合" or unit != limits[2] or low is None
            or high is None or not limits[0] <= low <= high <= limits[1]):
        return False
    found = set()
    for paragraph in paragraphs:
        text = unicodedata.normalize("NFKC", paragraph["text"]).replace("−","-")
        text = re.sub(r"(%|bp|基点)\s*[-~～–—至到]\s*(?=[+\-]?\d)", r"\1 ", text, flags=re.I)
        for a, b, raw_unit in MEASURE.findall(text):
            if ("%" if raw_unit=="%" else "bp") == unit:
                found.add(Decimal(a))
                if b: found.add(Decimal(b))
    return Decimal(str(low)) in found and Decimal(str(high)) in found


def finish(meta, index, candidates, review):
    rows = index["paragraphs"]
    by_pid = {p["pid"]:p for p in rows}
    def evidence(pids):
        return [{k:by_pid[pid][k] for k in ("pid","page","text")} for pid in pids]
    cards, warnings, intercepted = [], [], []
    used = set()
    for slot, bucket in SLOTS.items():
        proposed = review[slot]
        if proposed is None:
            continue
        card = json.loads(encode(proposed))
        card.update(meta, bucket=bucket, report_id=index["report_id"],
                    report_category=review["report_category"])
        pids = set(card["evidence_pids"])
        drivers = []
        for driver in card["drivers"]:
            valid = list(dict.fromkeys(pid for pid in driver["pids"] if pid in by_pid))
            if valid:
                drivers.append(dict(driver,pids=valid))
                pids.update(valid)
        valid = [pid for pid in by_pid if pid in pids]
        if not valid:
            intercepted.append(dict(bucket=bucket,reason="无有效原段",submitted_pids=sorted(pids)))
            continue
        card.update(drivers=drivers,evidence_pids=valid,evidence=evidence(valid))
        if not targets(card,context(index,valid)):
            warnings.append(dict(bucket=bucket,reason="目标区间待核",
                                 submitted=[card["target_low"],card["target_high"],card["target_unit"]]))
            card.update(target_low=None,target_high=None,target_unit=None)
        used.update(valid)
        cards.append(card)
    remaining = {pid for candidate in candidates for pid in candidate["pids"]} - used
    clues = [dict(reason="候选原段未归入策略卡", evidence_pids=[pid], evidence=evidence([pid]))
             for pid in by_pid if pid in remaining]
    return dict(**meta,report_id=index["report_id"],report_category=review["report_category"],
                cards=cards,unmerged_clues=clues,numeric_warnings=warnings,s3=intercepted)


def read_report(pdf,meta,db,model=MODEL,offline=False,invoke=ask):
    return read_index(index_pdf(pdf),meta,db,model,offline,invoke)


def read_index(idx,meta,db,model=MODEL,offline=False,invoke=ask):
    if not idx["paragraphs"]: raise ValueError("S0: no extractable text")
    packet = dict(report=meta, paragraphs=idx["paragraphs"])
    first,r1=cached_call(db,model,"stage1",S1,packet,STAGE1,offline,invoke)
    candidates=first["candidates"]
    review,r2=cached_call(db,model,"stage2",S2,dict(packet,candidates=candidates),STAGE2,offline,invoke)
    output=finish(meta,idx,candidates,review)
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
