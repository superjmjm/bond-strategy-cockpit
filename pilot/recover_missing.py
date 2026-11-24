"""Offline fourth pilot: route verified evidence, split clauses, then inspect gaps once."""
import argparse
import hashlib
import json
import re
import tempfile
import zipfile
from pathlib import Path

from check_card_replay import check_collisions
from evidence_cards import TENORS, connect, distinct, ingest, snapshot
from round_three import pages_and_spans

SHORT, MID, TEN, LONG, CURVE = TENORS
LEGS = r"(?<!\d)(?:30|三十)(?:年|[yY])?[-–—至](?:10|十)(?:年|[yY])?(?!\d)"
PAIR = re.compile(LEGS)
SHORT_BOND = re.compile(r"(?:1|一)[-–至](?:3|三)(?:年|[yY])(?:期)?(?:国债|利率债)")
OTHER = re.compile(r"地方债|国开|农发|口行|信用债|政金债|美债|二永|跨品种")
HIST = re.compile(r"在.{0,8}月.{0,6}(?:报告|月报)|上期(?:报告|建议)|此前(?:提示|建议)|历史策略|回测")
THRESHOLD = re.compile(r"\d+(?:\.\d+)?\s*(?:bp|BP|基点)(?:以上|上方|以下|下方)")
FUTURE = re.compile(r"可能|预计|或将|有望|仍有|或继续|可继续|后续或")
DECLINE = re.compile(r"收窄|压缩|下行空间|走平")
NEGATIVE = re.compile(r"不会|难以|并非|不再|未必|不宜|没有.{0,5}空间")

def relation(tenor, key, sentence, subject, predicate, direction, role="判断", action=None):
    assert subject in sentence and predicate in sentence
    return dict(tenor=tenor,id=key,page=int(key.split("S")[0][1:]),sentence=sentence,
                subject=subject,predicate=predicate,direction=direction,role=role,action=action)

def inspect_sentence(key, s, allow_multiple):
    if HIST.search(s):
        return []
    out=[]
    for match in SHORT_BOND.finditer(s):
        pred=s[match.end():].split("，")[0].rstrip("。；")
        if re.match(r"(?:补充|增加|带来|增强).{0,8}配置(?:盘力量|需求|力量)",pred):
            out.append(relation(SHORT,key,s,match[0],pred,1))
    if allow_multiple:
        # A bare "30Y" cannot establish the issuer; require explicit subject for an outright.
        m=re.search(r"短期(?:内)?(?:30[yY]|30年(?:期)?|三十年(?:期)?)国债(?:或受|需关注)[^，。；]{2,48}(?:扰动|压力)",s)
        if m and not OTHER.search(m[0]) and not NEGATIVE.search(m[0]):
            out.append(relation(LONG,key,s,re.search(r"(?:30[yY]|30年(?:期)?|三十年(?:期)?)国债",m[0])[0],m[0],0))
    m=PAIR.search(s)
    if not m:
        return out
    tail=s[m.start():].rstrip("。；")
    head=s[:m.start()]
    preceding=re.split(r"[，。；：:、（）()]",head)[-1]
    if OTHER.search(tail[:len(m[0])+7]) or OTHER.search(preceding) or re.search(r"期货|跨品种",head[-16:]):
        return out
    # A spread forecast and an entry criterion may be in consecutive clauses.
    forecast=re.match(LEGS+r"(?:国债)?(?:期限)?(?:利差)?(?:仍有|后续或仍有|预计|可能|或将)[^，。；]{0,15}(?:下行空间|收窄|压缩|走平)",tail)
    if forecast and not NEGATIVE.search(forecast[0]) and not re.search(r"曾|当时|回顾|此前预计|上周预计",preceding):
        out.append(relation(CURVE,key,s,m[0],forecast[0],1))
    # A general forecast can qualify a named target only when the author explicitly lists it.
    if re.search(r"(?:重点)?关注$",head) and FUTURE.search(head) and re.search(r"利差压缩行情",head) and not NEGATIVE.search(head):
        out.append(relation(CURVE,key,s,m[0],s[:m.end()],1))
    clause=re.search(r"(?:重新站回|可在|在)\d+(?:\.\d+)?(?:bp|基点)(?:以上|上方|以下|下方)[^，。；]{0,16}(?:配置性价比|布局|介入)",tail,re.I)
    if clause and not OTHER.search(tail[:clause.start()]) and not NEGATIVE.search(clause[0]):
        out.append(relation(CURVE,key,s,m[0],clause[0],None,"入场条件"))
    return out

def integrate(cards, observations, source):
    for tenor in TENORS:
        if cards.get(tenor) and "recovery" not in cards[tenor]:
            continue
        selected={}
        for f in observations:
            if f["tenor"]!=tenor:
                continue
            identity=(f["id"],f["subject"],f["predicate"],f["role"],f["direction"])
            if identity not in selected or len(f["sentence"])>len(selected[identity]["sentence"]):
                selected[identity]=f
        facts=distinct(list(selected.values()))
        if not facts or not any(f["role"]=="判断" for f in facts):
            continue
        decisions={f["direction"] for f in facts if f["direction"] is not None}
        origin=cards[tenor]["recovery"] if cards.get(tenor) else source
        cards[tenor]=dict(tenor=tenor,recovery=origin,direction=next(iter(decisions)) if len(decisions)==1 else None,
            conflict=len(decisions)>1,observations=[f for f in facts if f["role"]=="判断"],
            conditions=[f for f in facts if f["role"]!="判断"])

def run(raw_receipt, base_receipt, archive):
    output=[]
    for i,(r,prior) in enumerate(zip(raw_receipt["reports"],base_receipt["reports"],strict=True)):
        pdf=archive.read(f"{i}.pdf")
        assert hashlib.sha256(pdf).hexdigest()==r["sha256"]==prior["sha256"] and r["raw"]==prior["raw"]
        pages,spans=pages_and_spans(pdf)
        cards=prior["cards"].copy()
        events=[]
        pool=[]
        inputs={x["id"] for items in r["raw"].values() for x in items if x["id"] in spans}
        # Stage 1 uses only the physical-page verified quoted substring.
        for bucket,items in r["raw"].items():
            for x in items:
                s=re.sub(r"\s+","",x["quote"])
                if not s or s not in spans.get(x["id"],""):
                    continue
                for f in inspect_sentence(x["id"],s,False):
                    if f["tenor"]!=bucket:
                        pool.append(f)
        for label,keys in [("回执改归",[]),("已摘原句拆分",sorted(inputs)),("空栏补读",sorted(set(spans)-inputs))]:
            before={t for t,v in cards.items() if v}
            needs={t for t in TENORS if t not in before}
            curve=cards.get(CURVE)
            if curve and "recovery" in curve and not curve["conditions"]:
                needs.add(CURVE)
            cues={SHORT:SHORT_BOND,LONG:re.compile(r"30[yY年]|三十年"),CURVE:PAIR}
            reread=[]
            for k in keys:
                s=spans[k]
                if label=="空栏补读" and not any(p.search(s) for t,p in cues.items() if t in needs):
                    continue
                if label=="空栏补读":
                    reread.append(k)
                pool.extend(f for f in inspect_sentence(k,s,True) if f["tenor"] in needs)
            integrate(cards,pool,label)
            after={t for t,v in cards.items() if v}
            events.append(dict(stage=label,added=sorted(after-before),read_ids=reread))
        for f in pool:
            assert f["subject"] in f["sentence"] and f["predicate"] in f["sentence"] and f["sentence"] in pages[f["page"]-1]
        assert all(cards[t]==prior["cards"][t] for t in TENORS if prior["cards"][t])
        output.append(dict(source=r["source"],sha256=r["sha256"],broker=r["broker"],day=r["day"],cards=cards,portfolio=prior["portfolio"],events=events))
    return output

def main():
    p=argparse.ArgumentParser()
    for name in ("archive","raw","baseline","out"):
        p.add_argument(name,type=Path)
    args=p.parse_args()
    with zipfile.ZipFile(args.archive) as archive:
        rows=run(json.loads(args.raw.read_bytes()),json.loads(args.baseline.read_bytes()),archive)
    with tempfile.TemporaryDirectory() as tmp:
        db=connect(Path(tmp)/"trial.sqlite")
        def replay(batch):
            for r in batch:
                ingest(db,r["source"],r["sha256"],r["broker"],r["day"],r["cards"],r["portfolio"])
            return snapshot(db)
        first=replay(rows)
        assert first==replay(list(reversed(rows))*3) and not first["disputed"]
        db.close()
        checks=check_collisions(Path(tmp))
    result=dict(reports=rows,snapshot=first,replay_identical=True,collision_checks=checks)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf8")
    for r in rows:
        print(r["broker"],r["day"],[(e["stage"],e["added"],len(e["read_ids"])) for e in r["events"]])
        for t,c in r["cards"].items():
            if c and "recovery" in c:
                print(t,[(f["page"],f["predicate"]) for f in c["observations"]+c["conditions"]])

if __name__=="__main__":
    main()
