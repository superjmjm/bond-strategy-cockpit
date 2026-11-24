"""Offline acceptance on the original PDF archive and private Actions receipt."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import zipfile

from evidence_cards import TENORS, cached_extract, connect, encode, ingest, make_card, snapshot
from round_three import extractor_version, pages_and_spans, process


def write(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf8")


def check_collisions(folder):
    path=folder/"collisions.sqlite"
    db=connect(path)
    calls=[]
    waiting=threading.Event()
    complete=threading.Event()

    def first():
        conn=connect(path)
        def run():
            calls.append(1)
            waiting.set()
            assert complete.wait(10)
            return {"example":1}
        try:
            return cached_extract(conn,"sample","v1",run)
        finally:
            conn.close()
    with ThreadPoolExecutor() as pool:
        future=pool.submit(first)
        assert waiting.wait(10)
        try:
            cached_extract(db,"sample","v1",lambda: calls.append(2))
        except BlockingIOError:
            pass
        else:
            raise AssertionError("Second reader acquired active lease")
        finally:
            complete.set()
        assert future.result()[1]=="fresh"
    assert len(calls)==1
    def fail():
        raise ValueError("simulated interruption")
    try:
        cached_extract(db,"sample","v2",fail)
    except ValueError:
        pass
    assert cached_extract(db,"sample","v2",lambda: {})[1]=="fresh"
    t=TENORS[-1]
    def assertion(page,phrase):
        return dict(id=f"P{page}S1",page=page,role="主判断",quote="曲线"+phrase,subject="曲线",predicate=phrase)
    first=assertion(1,"短期可能走陡")
    second=assertion(3,"中期可能走平")
    conflicting,_=make_card(t,[first,first,second])
    assert len(conflicting["observations"])==2 and conflicting["conflict"] and conflicting["direction"] is None
    def store(source,h,card):
        cards=dict.fromkeys(TENORS)
        cards[t]=card
        ingest(db,source,h*64,"测试机构","2026-11-22",cards,[])
    one,_=make_card(t,[first])
    store("a","a",one)
    duplicate=assertion(2,"短期可能走陡")
    same,_=make_card(t,[duplicate])
    store("a","a",same)
    s=snapshot(db)
    assert len(s["cards"])==1 and len(s["votes"])==1 and len(s["cards"][0]["observations"][0]["citations"])==2
    store("copy","a",one)
    store("b","b",one)
    assert len(snapshot(db)["cards"])==2 and len(snapshot(db)["votes"])==1
    other,_=make_card(t,[second])
    store("a","a",other)
    s=snapshot(db)
    assert len(s["disputed"])==1 and len(s["cards"])==1 and not s["votes"]
    try:
        store("a","c",one)
    except ValueError:
        pass
    else:
        raise AssertionError("Changed source bytes accepted")
    assert db.execute("SELECT count(*) FROM report").fetchone()[0]==2
    db.close()
    return {"active_workers":2,"reader_calls":len(calls),"retry_after_failure":"pass","opposite_scenarios_in_one_card":2,
            "same_claim_two_pages_one_card":True,"same_broker_day_distinct_docs":2,"broker_votes_for_two_agreeing_docs":1,
            "broker_votes_after_conflicting_replay":0,"changed_source_rollback":"pass"}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("pdf_zip",type=Path)
    parser.add_argument("receipt",type=Path)
    parser.add_argument("output",type=Path)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    receipt=json.loads(args.receipt.read_bytes())
    version=extractor_version(receipt["model"])
    raw_by_digest={r["sha256"]:[r["raw"],r["request"]] for r in receipt["reports"]}
    assert len(receipt["reports"])==4 and receipt["replay_identical"]
    reports=[]
    with zipfile.ZipFile(args.pdf_zip) as z:
        ids=json.loads(z.read("ids.json"))
        for i,r in enumerate(receipt["reports"]):
            pdf=z.read(f"{i}.pdf")
            assert r["source"]==ids[i] and r["sha256"]==hashlib.sha256(pdf).hexdigest()
            assert r["request"]["model"]==receipt["model"]
            report=process(pdf,r["raw"])
            pages,spans=pages_and_spans(pdf)
            duplicated=process(pdf,{k:list(reversed(v+v)) for k,v in r["raw"].items()})
            assert report["cards"]==duplicated["cards"] and report["portfolio"]==duplicated["portfolio"]
            for card in report["cards"].values():
                if card is None:
                    continue
                for fact in card["observations"]+card["conditions"]:
                    for cite in fact["citations"]:
                        assert fact["quote"] in spans[cite["id"]] and spans[cite["id"]] in pages[cite["page"]-1]
            reports.append({k:r[k] for k in ("source","sha256","broker","day","request")}|report)
    def apply(db,r):
        ingest(db,r["source"],r["sha256"],r["broker"],r["day"],r["cards"],r["portfolio"])
    with tempfile.TemporaryDirectory() as temp:
        path=Path(temp)/"cards.sqlite"
        db=connect(path)
        for r in reports:
            value,how=cached_extract(db,r["sha256"],version,lambda:raw_by_digest[r["sha256"]])
            assert how=="fresh" and value==raw_by_digest[r["sha256"]]
            apply(db,r)
        original=snapshot(db)
        db.close()
        def replay(r):
            c=connect(path)
            try:
                value,how=cached_extract(c,r["sha256"],version,lambda: (_ for _ in ()).throw(AssertionError("cache miss")))
                assert value==raw_by_digest[r["sha256"]]
                apply(c,r)
                return how
            finally:
                c.close()
        with ThreadPoolExecutor(max_workers=4) as pool:
            hits=list(pool.map(replay,list(reversed(reports))*3))
        db=connect(path)
        assert original==snapshot(db) and hits==["cached"]*12
        slots=db.execute("SELECT count(*) FROM slot WHERE tenor!='组合层'").fetchone()[0]
        assert slots==4*len(TENORS) and not original["disputed"]
        saved=connect(args.output/"isolated-ledger.sqlite")
        db.backup(saved)
        saved.close()
        db.close()
        receipt_checks=check_collisions(Path(temp))
    write(args.output/"snapshot.json",original)
    write(args.output/"report-review.json",reports)
    write(args.output/"replay-checks.json",dict(reports=4,tenor_slots=slots,cards=len(original["cards"]),
          votes=len(original["votes"]),cache_hits=len(hits),parallel_workers=4,snapshot_identical=True,synthetic=receipt_checks))
    print(encode(dict(reports=4,slots=slots,cards=len(original["cards"]),votes=len(original["votes"]),cache_hits=len(hits))))

if __name__=="__main__":
    main()
