"""Runs only the sealed 18-PDF trial. No production writes or public report data."""
import hashlib
import io
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload
from reader import open_cache, read_report, digest, encode
from scripts.report_io import drive_service

HERE = Path(__file__).parent
PRIOR_RECEIPT = "1wQF5QVVGO4gngUQ3GuP3rUvfAFXUou6M"
CATALOG = "1OsUbDu_TdxCLeXG642rvvEJ8NKqoVzZV"
PARENT_HASH = "ed4ba419f13cf234196e3958d1c6a82871a43fd075210b3574fe325540baec83"

def private_parent(drive):
    prior = drive.files().get(fileId=PRIOR_RECEIPT, fields="ownedByMe,shared,parents").execute()
    parents = prior.get("parents", [])
    if not prior["ownedByMe"] or prior.get("shared") or len(parents) != 1:
        raise ValueError("Receipt provenance")
    if hashlib.sha256(parents[0].encode()).hexdigest() != PARENT_HASH:
        raise ValueError("Unexpected trial destination")
    return parents[0]

def save(drive,parent,name,value):
    # File-scoped OAuth can verify sibling metadata but cannot enumerate the parent.
    # Compare the new empty file's actual parent and ownership before uploading any content.
    meta=drive.files().create(body={"name":name,"mimeType":"application/json"},
                              fields="id,parents,ownedByMe,shared").execute()
    if meta.get("parents")!=[parent] or not meta["ownedByMe"] or meta.get("shared"):
        raise ValueError("Output privacy")
    media=MediaIoBaseUpload(io.BytesIO(encode(value).encode()),mimetype="application/json")
    drive.files().update(fileId=meta["id"],media_body=media).execute(num_retries=2)

def load_pdf(drive,row):
    for fid in dict.fromkeys([row["pdf_id"],*row.get("pdf_candidates",[])]):
        try: data=drive.files().get_media(fileId=fid,supportsAllDrives=True).execute(num_retries=2)
        except HttpError as err:
            if err.resp.status in (403,404): continue
            raise
        if hashlib.sha256(data).hexdigest()!=row["sha256"]:
            raise ValueError("Source fingerprint differs")
        return data
    raise ValueError("No accessible matching original")

def process(pdf,meta,directory):
    path=directory/(hashlib.sha256(pdf).hexdigest()+".db")
    db=open_cache(path)
    try:
        result=read_report(pdf,meta,db)
        replay=read_report(pdf,meta,db,offline=True)
    finally: db.close()
    if digest(result)!=digest(replay): raise ValueError("Replay differs")
    return result

def run():
    if os.environ.get("GITHUB_RUN_ATTEMPT")!="1": raise ValueError("Single attempt only")
    study=json.loads((HERE/"study.json").read_bytes())
    rows=study["reports"]
    if len(rows)!=18 or len({r["report_id"] for r in rows})!=18: raise ValueError("Study scope")
    drive=drive_service()
    parent=private_parent(drive)
    catalog=json.loads(drive.files().get_media(fileId=CATALOG).execute())["reports"]
    archive={r["sha256"]:r for r in catalog}
    run_id=os.environ["GITHUB_RUN_ID"]
    prefix="anchored18-"+run_id
    manifest=dict(commit=os.environ["GITHUB_SHA"],reference_sha256=study["reference_sha256"],
                  reference_type=study["reference_type"],reports=[])
    prepared=[]
    for i,row in enumerate(rows,1):
        raw=archive[row["report_id"]]
        pdf=load_pdf(drive,raw)
        meta={k:row[k] for k in ("broker","series","report_date")}
        meta["title"]=raw["title"]
        prepared.append((i,pdf,meta))
    print("Verified original PDFs:",len(prepared),flush=True)
    with tempfile.TemporaryDirectory() as td, ThreadPoolExecutor(max_workers=3) as pool:
        jobs={pool.submit(process,pdf,meta,Path(td)):i for i,pdf,meta in prepared}
        for job in as_completed(jobs):
            i=jobs[job]
            try:
                result=job.result()
            except Exception as exc:
                manifest["reports"].append(dict(index=i,error=type(exc).__name__,
                    code=getattr(exc,"code",None),status=getattr(exc,"status_code",None)))
                print("Incomplete report",i,type(exc).__name__,flush=True)
                continue
            out=result["output"]
            # Small private per-card files preserve every evidence paragraph for independent scoring.
            label=f"{prefix}-{i:02d}"
            for j,card in enumerate(out["cards"],1): save(drive,parent,f"{label}-card{j}.json",card)
            audit=dict(report_id=out["report_id"],report_category=out["report_category"],
                       cards=[{k:c[k] for k in ("bucket","action","logic","cond","risk","direction",
                       "vote_eligible","account_note","evidence_pids","numbers","target_low","target_high")}
                              for c in out["cards"]],
                       candidates=result["s1"]["candidates"],clues=[{k:v for k,v in clue.items() if k!="evidence"}
                       for clue in out["unmerged_clues"]],rejected=out["rejected"],s3=out["s3"],
                       numeric_warnings=out["numeric_warnings"],pages=result["s0"]["pages"],receipts=result["receipts"])
            save(drive,parent,label+"-audit.json",audit)
            save(drive,parent,label+"-viewer.json",out)
            # S0 is reproducible from the original; persist just the S2 response with the audit trail.
            save(drive,parent,label+"-review.json",dict(review=result["s2"],clues=out["unmerged_clues"]))
            manifest["reports"].append(dict(index=i,cards=len(out["cards"]),candidates=len(audit["candidates"]),
                                           replay_identical=True,sha256=digest(result)))
            print("Saved report",i,"cards",len(out["cards"]),flush=True)
    manifest["reports"].sort(key=lambda row:row["index"])
    save(drive,parent,prefix+"-manifest.json",manifest)
    if any("error" in row for row in manifest["reports"]): raise SystemExit(1)

if __name__=="__main__":
    run()
