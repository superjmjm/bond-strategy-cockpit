"""Offline trial: link a tenor-specific outlook to an existing portfolio conclusion."""
import argparse
from copy import deepcopy
import hashlib
import json
import re
import tempfile
import zipfile
from pathlib import Path

from check_card_replay import check_collisions
from evidence_cards import TENORS, connect, ingest, snapshot
from round_three import pages_and_spans

# A level or a market move alone does not identify an author's stance.
LEVEL = re.compile(r"(?P<tenor>10|30)(?:年|[yY])(?:期)?国债收益率(?P<view>(?:即将|可能|或将)?(?:迫近|接近|挑战)(?:前低|前高|\d+(?:\.\d+)?[%％]关口))")
SLOW = re.compile(r"(?:可能|或将|或).{0,10}长久期国债行情(?:结构性)?放缓")
BULL = re.compile(r"债市(?:多头趋势(?:可能|或)?(?:不改|延续)|仍(?:然)?偏多)")
ACTION = re.compile(r"(?:维持|保持)久期(?:仍是优选|仍为优选|为宜|不变)")
REJECT = re.compile(r"去年|此前曾|上期认为|历史回顾|若非|不再|并不|不能|不宜|转为看空|下调久期|降低久期|并非优选")
BREAK = re.compile(r"另一个情景|另一种情景|反之|另一种情况下|情景[二三]|(?:^|[。；])(?:[二三四五]、|第[二三四五]部分)")

def cite(sid,text):
    return dict(id=sid,page=int(sid.split("S")[0][1:]),quote=text)

def links(spans, portfolio, digest):
    proposals={}
    for entry in portfolio:
        key=entry["id"]
        conclusion=entry["quote"]
        if not spans.get(key) or conclusion not in spans[key] or not BULL.search(conclusion) or not ACTION.search(conclusion):
            continue
        if REJECT.search(spans[key]) or not re.match(r"^(?:因而|因此|所以|综合来看|整体而言|总体来看)",spans[key]):
            continue
        pg,num=key.split("S")
        block=[(pg+"S"+str(i),spans.get(pg+"S"+str(i),"")) for i in range(max(1,int(num)-8),int(num))]
        ref=digest+":"+key
        for pos,(sid,sentence) in enumerate(block):
            matches=list(LEVEL.finditer(sentence))
            if len({m["tenor"] for m in matches})<2:
                continue
            chain=block[pos:]+[(key,spans[key])]
            if any(not s or REJECT.search(s) or BREAK.search(s) for _,s in chain):
                continue
            slowdown=next(((i,s) for i,s in chain if SLOW.search(s)),None)
            if not slowdown:
                continue
            mitigator=next(((i,s) for i,s in chain if "减速带" in s and "而非刚性壁垒" in s),None)
            for m in matches:
                tenor=TENORS[2] if m["tenor"]=="10" else TENORS[3]
                fact=dict(**cite(sid,m[0]),role="期限判断",target=None,action=None)
                level=re.search(r"\d+(?:\.\d+)?[%％]",m["view"])
                if level:
                    fact.update(level=level[0],number_role="关口")
                card=dict(tenor=tenor,direction=1,direction_scope="共同的长久期判断",portfolio_ref=ref,
                          conflict=False,observations=[fact],conditions=[cite(*slowdown)])
                if mitigator:
                    card["conditions"].append(cite(*mitigator))
                proposals.setdefault(tenor,[]).append(card)
    # Multiple incompatible evidence chains require review, never pick the last one.
    return {t:v[0] for t,v in proposals.items() if all(c==v[0] for c in v)}

def controls():
    pref="随着10年国债收益率迫近前低，30年国债收益率即将挑战2.0%关口。"
    slow="可能会使长久期国债行情结构性放缓。"
    end="因而站在当下，债市多头趋势可能不改，维持久期仍是优选。"
    def test(a=pref,b=slow,c=end,other_page=False):
        key="P2S3" if other_page else "P1S3"
        return links({"P1S1":a,"P1S2":b,key:c},[dict(id=key,quote=c)],"fixture")
    assert len(test())==2
    for result in (test(a=pref.replace("30年国债","30年地方债")),test(b="国开债行情或将放缓。"),
                   test(c=end.replace("多头趋势可能不改","方向尚待确认")),test(b="另一种情景，"+slow),
                   test(c=end.replace("维持久期仍是优选","维持久期并非优选")),test(other_page=True),
                   test(a="历史回顾："+pref)):
        assert not result

def main():
    parser=argparse.ArgumentParser()
    for field in ("archive","baseline","output"):
        parser.add_argument(field,type=Path)
    args=parser.parse_args()
    base=json.loads(args.baseline.read_bytes())
    reports=deepcopy(base["reports"])
    refs={}
    new=[]
    controls()
    with zipfile.ZipFile(args.archive) as z:
        sources=json.loads(z.read("ids.json"))
        for i,r in enumerate(reports):
            pdf=z.read(f"{i}.pdf")
            assert sources[i]==r["source"] and hashlib.sha256(pdf).hexdigest()==r["sha256"]
            pages,spans=pages_and_spans(pdf)
            for t,c in links(spans,r["portfolio"],r["sha256"]).items():
                if r["cards"][t] is not None:
                    continue
                ref=c["portfolio_ref"]
                sid=ref.split(":")[1]
                p=next(x for x in r["portfolio"] if x["id"]==sid)
                refs[ref]=dict(report_id=r["sha256"],source_id=sid)
                assert p["quote"] in pages[p["page"]-1]
                for evidence in c["observations"]+c["conditions"]:
                    assert evidence["quote"] in spans[evidence["id"]] and evidence["quote"] in pages[evidence["page"]-1]
                r["cards"][t]=c
                new.append(dict(source=r["source"],tenor=t,ref=ref))
    with tempfile.TemporaryDirectory() as tmp:
        db=connect(Path(tmp)/"trial.sqlite")
        def replay(batch):
            for r in batch:
                ingest(db,r["source"],r["sha256"],r["broker"],r["day"],r["cards"],r["portfolio"])
            return snapshot(db)
        current=replay(reports)
        assert current==replay(list(reversed(reports))*3) and not current["disputed"]
        db.close()
        checks=check_collisions(Path(tmp))
    assert current["portfolio"]==base["snapshot"]["portfolio"]
    assert all(c in current["cards"] for c in base["snapshot"]["cards"])
    assert len(current["cards"])==len(base["snapshot"]["cards"])+len(new)
    result=dict(snapshot=current,linked_cards=new,portfolio_index=refs,replay_identical=True,regression=checks)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf8")
    print(dict(new=new,cards=len(current["cards"]),votes=len(current["votes"]),portfolio=len(current["portfolio"])))

if __name__=="__main__":
    main()
