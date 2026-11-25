"""Runs only the sealed 18-PDF trial. No production writes or public report data."""
import hashlib
import gzip
import io
import json
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from googleapiclient.http import MediaIoBaseUpload
from reader import open_cache, read_index, digest, encode
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

def private_file(drive,parent,name,mime):
    # File-scoped OAuth can verify sibling metadata but cannot enumerate the parent.
    # Compare the new empty file's actual parent and ownership before uploading any content.
    meta=drive.files().create(body={"name":name,"mimeType":mime},
                              fields="id,parents,ownedByMe,shared").execute()
    if meta.get("parents")!=[parent] or not meta["ownedByMe"] or meta.get("shared"):
        raise ValueError("Output privacy")
    return meta["id"]

def save(drive,parent,name,value):
    file_id=private_file(drive,parent,name,"application/json")
    media=MediaIoBaseUpload(io.BytesIO(encode(value).encode()),mimetype="application/json")
    drive.files().update(fileId=file_id,media_body=media).execute(num_retries=2)
    return file_id

def sealed_indices(drive,parent,prefix):
    file_id=os.environ["REGRESSION_INPUT_ID"]
    until=time.monotonic()+1200
    while time.monotonic()<until:
        status=drive.files().get(fileId=file_id,fields="size,ownedByMe,shared,parents").execute()
        if not status["ownedByMe"] or status.get("shared") or status.get("parents")!=[parent]:
            raise ValueError("Source receipt privacy")
        if int(status.get("size",0))>0:
            data=drive.files().get_media(fileId=file_id).execute(num_retries=2)
            if hashlib.sha256(data).hexdigest()!=os.environ["INDEX_SHA256"]:
                raise ValueError("Frozen input digest")
            return json.loads(gzip.decompress(data))
        time.sleep(8)
    raise TimeoutError("Waiting for authenticated S0 receipt")

def process(index,meta,directory):
    path=directory/(index["report_id"]+".db")
    db=open_cache(path)
    try:
        result=read_index(index,meta,db)
        replay=read_index(index,meta,db,offline=True)
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
    prefix="anchored-regression18-"+run_id
    manifest=dict(commit=os.environ["GITHUB_SHA"],reference_sha256=study["reference_sha256"],
                  reference_type=study["reference_type"],reports=[])
    prepared=[]
    indices=sealed_indices(drive,parent,prefix)
    if [x["report_id"] for x in indices]!=[r["report_id"] for r in rows]:
        raise ValueError("Input report order or identity")
    for i,(row,index) in enumerate(zip(rows,indices),1):
        raw=archive[row["report_id"]]
        meta={k:row[k] for k in ("broker","series","report_date")}
        meta["title"]=raw["title"]
        prepared.append((i,index,meta))
    print("Verified indexed originals:",len(prepared),flush=True)
    with tempfile.TemporaryDirectory() as td, ThreadPoolExecutor(max_workers=3) as pool:
        jobs={pool.submit(process,index,meta,Path(td)):i for i,index,meta in prepared}
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
            receipt = dict(index=i, output=out, candidates=result["s1"]["candidates"],
                           review=result["s2"], receipts=result["receipts"], replay_identical=True)
            receipt_id = save(drive,parent,f"{prefix}-{i:02d}.json",receipt)
            manifest["reports"].append(dict(index=i, receipt_id=receipt_id, cards=len(out["cards"]),
                                           replay_identical=True,sha256=digest(receipt)))
            print("Saved report",i,"cards",len(out["cards"]),flush=True)
    manifest["reports"].sort(key=lambda row:row["index"])
    print("Manifest", save(drive,parent,prefix+"-manifest.json",manifest), flush=True)
    if any("error" in row for row in manifest["reports"]): raise SystemExit(1)

if __name__=="__main__":
    run()
