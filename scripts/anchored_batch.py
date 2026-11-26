"""Read archived/inbox reports with the accepted two-pass reader; publish only card evidence."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from report_io import INBOX, ROOT, download_cached, drive_service, env, list_pdfs
from report_scope import manual_identity, weekly_broker

sys.path.insert(0, str(ROOT / "pilot" / "anchored"))
from reader import open_cache, read_report


def identity(filename, history):
    manual = manual_identity(filename)
    if manual:
        return manual
    existing = next((r for r in history if r.get("source_file")==filename and not r.get("is_demo")), None)
    if existing:
        return dict(broker=existing["broker"], series="周报", title=existing["report_title"], report_date=existing["date"])
    broker = weekly_broker(filename)
    match = re.fullmatch(r"\d+-[^-]+-(.+)-(\d{6})\.pdf", filename, re.I)
    if not broker or not match:
        raise ValueError("Cannot establish report identity from archive filename: " + filename)
    day = datetime.strptime(match[2], "%y%m%d").date()
    return dict(broker=broker,series="周报",title=match[1],report_date=day.isoformat())


def public_report(result, filename):
    output = result["output"]
    report = {k:output[k] for k in ("broker","series","title","report_date","report_id","report_category")}
    report["source_file"] = filename
    paragraphs = {p["pid"]:p for p in result["s0"]["paragraphs"]}
    report["cards"] = []
    for card in output["cards"]:
        for e in card["evidence"]:
            original=paragraphs[e["pid"]]
            if original["text"]!=e["text"] or original["page"]!=e["page"]:
                raise ValueError("Evidence does not match indexed PDF")
        item = {k:card[k] for k in ("bucket","raw_tenor","direction","vote_eligible","logic","action","cond",
                                    "account_note","horizon","target_low","target_high","target_unit","target_note",
                                    "risk","drivers","instrument_scope")}
        item["evidence"] = [{"page":e["page"],"pid":e["pid"],"text":e["text"][:180],
                             "excerpt":len(e["text"])>180} for e in card["evidence"]]
        report["cards"].append(item)
    return report


def save(path, data):
    path.parent.mkdir(exist_ok=True, parents=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", mode="w", encoding="utf8", delete=False) as f:
        json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")
    os.replace(f.name, path)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--archive-id", action="append", default=[])
    parser.add_argument("--output", type=Path, default=ROOT/"anchored_reports.json")
    args=parser.parse_args()
    data=json.loads(args.output.read_bytes()) if args.output.exists() else []
    known={r["report_id"] for r in data}
    history=json.loads((ROOT/"dashboard_data.json").read_bytes())["records"]
    if args.archive_id:
        drive=drive_service()
        rows=list_pdfs(drive,env("GDRIVE_FOLDER_ID"))
        inputs=[]
        for rid in args.archive_id:
            row, = [r for r in rows if r.get("appProperties",{}).get("nxny_id")==rid or r["name"].startswith(rid+"-")]
            raw=download_cached(drive,row)
            expected={r["report_id"] for r in history if r.get("source_file")==row["name"]}
            if not expected or hashlib.sha256(raw).hexdigest() not in expected:
                raise ValueError("Archive does not match published PDF digest")
            inputs.append((row["name"],raw))
    else:
        inputs=((p.name,p.read_bytes()) for p in sorted(INBOX.glob("*.pdf")))
    failed=0
    with tempfile.TemporaryDirectory() as tmp:
        cache=open_cache(Path(tmp)/"calls.sqlite")
        try:
            for filename, raw in inputs:
                rid=hashlib.sha256(raw).hexdigest()
                if rid in known:
                    continue
                try:
                    result=read_report(raw,identity(filename,history),cache)
                    report=public_report(result,filename)
                    assert report["report_id"]==rid
                    data.append(report)
                    save(args.output,data)
                    known.add(rid)
                    print("ANCHORED_RESULT",filename,len(report["cards"]),sum(c["vote_eligible"] for c in report["cards"]),
                          "model",result["receipts"][1]["model"],"effort",result["receipts"][1]["reasoning_effort"])
                except Exception as exc:
                    print("::error::",filename,type(exc).__name__,str(exc)[:220])
                    failed+=1
        finally:
            cache.close()
    return 2 if failed else 0


if __name__=="__main__":
    sys.exit(main())
