"""Single isolated entry point for fresh PDFs and verified extraction receipts."""
import argparse
from datetime import date
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import tempfile

from jsonschema import validate

from evidence_cards import cached_extract, connect, encode, ingest, snapshot
from link_portfolio import links
from generic_duration import unspecified_long_end
from recover_missing import recover
from round_three import extractor_version, pages_and_spans, process_spans, select, selection_schema
from round_two import MODEL


def pipeline_id():
    root=Path(__file__).resolve().parent
    names=("unified.py","requirements-unified.txt","round_two.py","round_three.py","evidence_cards.py",
           "recover_missing.py","link_portfolio.py","generic_duration.py","../schemas/view_extraction.schema.json")
    resources={n:(root/n).read_text(encoding="utf8") for n in names}
    return hashlib.sha256(encode(resources).encode()).hexdigest()


def execute(manifest, cache, seed, offline):
    revision=extractor_version(MODEL)
    indexed={}
    if seed:
        receipt=json.loads(seed.read_bytes())
        if receipt["extractor_version"]!=revision or receipt["model"]!=MODEL:
            raise ValueError("Incompatible extraction receipt")
        for row in receipt["reports"]:
            indexed[row["sha256"]]=[row["raw"],row["request"]]
    records=json.loads(manifest.read_bytes())
    if not isinstance(records,list) or not records:
        raise ValueError("Expected a nonempty PDF manifest")
    reports=[]
    client=None
    ledger=connect(":memory:")
    try:
        for row in records:
            broker=row["broker"].strip()
            day=date.fromisoformat(row["day"]).isoformat()
            source=row["source"].strip()
            if not broker or not source:
                raise ValueError("Missing report identity")
            pdf=(manifest.parent/row["pdf"]).read_bytes()
            digest=hashlib.sha256(pdf).hexdigest()
            pages,spans=pages_and_spans(pdf)
            if not spans:
                raise ValueError("No physical-page text available for evidence verification")
            origin="cache"
            def read():
                nonlocal origin,client
                if digest in indexed:
                    origin="seed"
                    value=indexed[digest]
                elif offline:
                    raise ValueError("Offline cache miss: "+digest)
                else:
                    from openai import OpenAI
                    client=client or OpenAI(timeout=240,max_retries=1)
                    origin="model"
                    value=select(client,pdf,spans)
                raw,request=value
                validate(raw,selection_schema())
                if request["model"]!=MODEL or not request["id"]:
                    raise ValueError("Unexpected model response")
                return value
            (raw,request),_=cached_extract(cache,digest,revision,read)
            validate(raw,selection_schema())
            if request["model"]!=MODEL or not request["id"]:
                raise ValueError("Invalid cached model receipt")
            parsed=process_spans(pages,spans,raw)
            cards,events=recover(pages,spans,raw,parsed["cards"])
            portfolio=parsed["portfolio"]
            for tenor,card in links(spans,portfolio,digest).items():
                if cards[tenor] is None:
                    cards[tenor]=card
            ingest(ledger,source,digest,broker,day,cards,portfolio+unspecified_long_end(spans))
            reports.append(dict(source=source,sha256=digest,broker=broker,day=day,request=request,
                                evidence_source=origin,recovery=events,verified_pages=len(pages)))
        return dict(pipeline_id=pipeline_id(),extractor_version=revision,model=MODEL,
                    packages={p:version(p) for p in ("pypdf","jsonschema")},reports=reports,snapshot=snapshot(ledger))
    finally:
        ledger.close()
        if client:
            client.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("manifest",type=Path)
    p.add_argument("--cache",required=True,type=Path)
    p.add_argument("--output",required=True,type=Path)
    p.add_argument("--seed",type=Path)
    p.add_argument("--offline",action="store_true")
    args=p.parse_args()
    if args.output.exists():
        p.error("Result already exists; choose a new output for a new experiment")
    cache=connect(args.cache)
    try:
        result=execute(args.manifest,cache,args.seed,args.offline)
    finally:
        cache.close()
    with tempfile.NamedTemporaryFile(dir=args.output.parent,delete=False) as tmp:
        temp=Path(tmp.name)
    try:
        temp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf8")
        if os.name=="nt":
            os.rename(temp,args.output)  # Windows rename refuses to replace an existing result.
        else:
            os.link(temp,args.output)
    finally:
        temp.unlink(missing_ok=True)

if __name__=="__main__":
    main()
