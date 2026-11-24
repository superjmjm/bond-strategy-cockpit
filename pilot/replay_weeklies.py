"""Isolated four-report replay. Run with the unmodified handoff files at repo root."""
from __future__ import annotations
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

from googleapiclient.http import MediaIoBaseUpload
from jsonschema import Draft202012Validator
from openai import OpenAI
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.report_io import drive_service, download_cached

SAMPLES = [
    ("6536940-华创证券-债券周报：赎回之后，再看年末抢跑-261123.pdf", "华创证券", "2026-11-23"),
    ("6537477-华泰证券-利率周报：隔夜换锚后的重定价空间-261122.pdf", "华泰证券", "2026-11-22"),
    ("6537373-兴业证券-债券周报：看股做债时刻-261122.pdf", "兴业证券", "2026-11-22"),
    ("华创证券-债券周报：央行喊话，债市如何反应？-261025.pdf", "华创证券", "2026-10-25"),
]

def canonical(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).translate(
        str.maketrans({"–": "-", "—": "-", "－": "-", "～": "~", "％": "%"})).lower()

def find_source(drive, name):
    files = drive.files().list(
        q=f"trashed=false and mimeType='application/pdf' and name='{name}'",
        fields="nextPageToken,files(id,name,size,md5Checksum)", pageSize=100,
    ).execute(num_retries=2)
    candidates = files.get("files", [])
    if len(candidates) != 1 or files.get("nextPageToken"):
        raise ValueError("Archive identity ambiguous or missing")
    return candidates[0]

def extract(client, pdf, prompt, schema):
    upload = client.files.create(file=("weekly.pdf", pdf, "application/pdf"), purpose="user_data")
    try:
        response = client.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL") or "gpt-4.1-mini", temperature=0.1,
            messages=[
                {"role":"system", "content":prompt},
                {"role":"user","content":[{"type":"file","file":{"file_id":upload.id}}]},
            ],
            response_format={"type":"json_schema","json_schema":{
                "name":"bond_report_extraction","strict":True,"schema":schema}},
        )
        choice = response.choices[0]
        if choice.finish_reason != "stop" or choice.message.refusal:
            raise ValueError("Incomplete or refused model response")
        return json.loads(choice.message.content), response.id, response.model
    finally:
        client.files.delete(upload.id)

def inspect_view(v, pages):
    reasons = []
    quote = canonical(v["quote"])
    hits = [i for i,p in enumerate(pages,1) if quote and quote in p]
    if v["page"] not in hits:
        reasons.append("quote_page")
    low, high = v["target_low"], v["target_high"]
    if v["target_type"] in ("单点目标", "明确区间"):
        if low is None or high is None or low > high or (v["target_type"]=="单点目标" and low!=high):
            reasons.append("target_shape")
        else:
            numbers = {float(s) for s in re.findall(r"(?<![\d.])\d+(?:\.\d+)?(?![\d.])",quote)}
            if low not in numbers or high not in numbers:
                reasons.append("target_values_need_page_review")
            if any(w in quote for w in ("阻力","逢高","择机布局","上方布局")):
                reasons.append("conditional_target_needs_review")
    elif low is not None or high is not None:
        reasons.append("unexpected_numeric_target")
    return {"tenor":v["tenor"],"quote_pages":hits,"flags":reasons}

def main():
    prompt = (ROOT/"schemas/extraction_prompt.md").read_bytes().decode("utf-8")
    schema = json.loads((ROOT/"schemas/view_extraction.schema.json").read_bytes())
    validator = Draft202012Validator(schema)
    builder = (ROOT/"pipeline/build_snapshot.py").read_bytes()
    drive=drive_service()
    sources=[find_source(drive,name) for name,_,_ in SAMPLES]
    client=OpenAI(timeout=240.0,max_retries=1)
    review={"reports":[],"build_log":None}
    dest=drive.files().create(body={"name":"国债周报四篇-隔离试跑-"+os.environ.get("GITHUB_RUN_ID","local")+".json",
        "mimeType":"application/json"},fields="id,shared").execute()
    if dest.get("shared"):
        raise RuntimeError("Review destination must be private")
    def checkpoint():
        raw=json.dumps(review,ensure_ascii=False,indent=2).encode()
        drive.files().update(fileId=dest["id"],media_body=MediaIoBaseUpload(io.BytesIO(raw),mimetype="application/json")).execute(num_retries=2)
    views={}
    for (name,broker,date),source in zip(SAMPLES,sources):
        receipt={"name":name}
        try:
            pdf=download_cached(drive,source)
            pages=[canonical(p.extract_text() or "") for p in PdfReader(io.BytesIO(pdf)).pages]
            report,request,model=extract(client,pdf,prompt,schema)
            receipt.update(raw=report,request_id=request,model=model,sha256=hashlib.sha256(pdf).hexdigest(),
                pdf_url="https://drive.google.com/file/d/"+source["id"]+"/view")
            validator.validate(report)
            if (report["broker"],report["report_date"])!=(broker,date):
                raise ValueError("Report identity mismatch")
            checks=[]
            for v in report["views"]:
                check=inspect_view(v,pages)
                checks.append(check)
                if not check["flags"]:
                    views[(broker,date,v["tenor"])]={**{k:report[k] for k in ("broker","series","report_date")},
                        "pdf_url":receipt["pdf_url"],**v}
            receipt["checks"]=checks
            print(f"{broker} {date}: extracted={len(checks)}, mechanically accepted={sum(not c['flags'] for c in checks)}",flush=True)
        except Exception as exc:
            receipt["error"]=type(exc).__name__
            receipt["error_code"]=getattr(exc,"code",None)
            print(f"{broker} {date}: {type(exc).__name__}",flush=True)
        review["reports"].append(receipt)
        checkpoint()
    with tempfile.TemporaryDirectory() as temp:
        work=Path(temp)
        (work/"data").mkdir()
        (work/"pipeline").mkdir()
        (work/"pipeline/build_snapshot.py").write_bytes(builder)
        rows=list(views.values())
        (work/"data/views.json").write_text(json.dumps(rows,ensure_ascii=False),encoding="utf-8")
        (work/"data/yields.json").write_text("[]",encoding="utf-8")
        run=subprocess.run([sys.executable,"pipeline/build_snapshot.py"],cwd=work,capture_output=True,text=True,check=True)
        review.update(views=rows,build_log=run.stdout+run.stderr)
        snapshot=json.loads((work/"frontend/data/snapshot.json").read_bytes())
        assert len(snapshot["views"])==len(rows) and not snapshot["has_yields"] and not run.stderr
        checkpoint()
        print(run.stdout.strip())
    if any("error" in r for r in review["reports"]):
        raise SystemExit(2)

if __name__=="__main__":
    main()
