"""Bind strategy proposals to exact sentences and index named instruments."""
import re
from difflib import SequenceMatcher

TENOR = r"\d{1,2}(?:[-–至]\d{1,2})?(?:年(?:期)?|[yY])"
INST = r"(?:政金债|国开债?|农发债?|地方债|国债|利率债)"
INDEX = re.compile(rf"({TENOR})({INST})(?:与|和|及|、)?({INST})?")
SHARED = re.compile(rf"({TENOR})(?:和|与|、)({TENOR})({INST})")
CURVE = re.compile(r"30(?:年|y)?(?:-|–|和|与|至)10(?:年|y)",re.I)
BAND = re.compile(r"(\d+(?:\.\d+)?)\s*[-–至]\s*(\d+(?:\.\d+)?)bp",re.I)
ACTION = re.compile(r"建议|(?:可(?:再|继续)?)?关注|多看少动|配置价值|供给高峰过后|逢调整买入|骑乘收益|品种利差压缩空间")
DISALLOW = re.compile(r"期货|信用|美债|存单|曾建议|历史上|并非|不建议.{0,6}压缩")
FUTURE = re.compile(r"短期|近期|年内|未来|后续|供给高峰过后")

def source_proposal(c, spans):
    core=c["core"]
    if not ACTION.search(core) and not (CURVE.search(core) and BAND.search(core)):
        return c
    evidence=list(c["evidence"])
    for e in c["evidence"]:
        pg,num=e["id"].split("S") if re.fullmatch(r"P[0-9]+S[0-9]+",e["id"]) else ("",0)
        cited=spans.get(e["id"])
        if not cited or not e["quote"].startswith(cited):
            continue
        rest=e["quote"][len(cited):]
        for i in range(int(num)+1,int(num)+4):
            key=pg+"S"+str(i)
            following=spans.get(key)
            if not following or not rest.startswith(following):
                break
            evidence.append(dict(id=key,quote=following))
            rest=rest[len(following):]
    for e in evidence:
        sentence=spans.get(e["id"],"")
        literal_overlap = e["quote"] and (e["quote"] in sentence or (len(sentence)>15 and sentence in e["quote"]))
        if (not sentence or not literal_overlap or
                DISALLOW.search(sentence) or not ACTION.search(sentence)):
            continue
        if core in e["quote"] and e["quote"] in sentence:
            return c
        # Similarity only selects a source sentence. The complete literal
        # sentence, including contrary clauses and conditions, becomes the view.
        if (len(sentence) <= 280 and
             SequenceMatcher(None,core,sentence,autojunk=False).ratio() >= .48):
            return {**c,"core":sentence.rstrip("。；"),"evidence":[{"id":e["id"],"quote":sentence}]}
    return c


def instrument_index(core):
    subjects=[]
    for m in INDEX.finditer(core):
        subjects.append(m[1]+m[2])
        if m[3]:
            subjects.append(m[1]+m[3])
    for m in SHARED.finditer(core):
        subjects.extend((m[1]+m[3],m[2]+m[3]))
    return list(dict.fromkeys(subjects))


def curve_windows(core):
    m=CURVE.search(core)
    if not m or DISALLOW.search(core):
        return []
    tail=core[m.end():]
    if not tail.startswith(("短期","利差短期","国债利差短期")) or not re.search(r"压缩|收窄",tail):
        return []
    bands=list(BAND.finditer(tail))
    if len(bands)!=2:
        return []
    windows=[]
    offset=0
    for b in bands:
        context=tail[offset:b.start()].strip("，、")
        if not FUTURE.search(context):
            return []
        low,high=map(float,b.groups())
        if low>high:
            return []
        windows.append(dict(context=context,low_bp=low,high_bp=high))
        offset=b.end()
    return windows


def project_curve(views,cards):
    if cards.get("曲线形态"):
        return
    eligible=[v for v in views if v.get("spread_windows")]
    if not eligible:
        return
    if any(CURVE.search(v["core"]) and re.search(r"(?:预计|可能|有望|将).{0,8}(?:走阔|扩大|走扩)",v["core"]) for v in views):
        return
    # Only vote when both endpoints move lower and all windows agree.
    signatures=set()
    for v in eligible:
        a,b=v["spread_windows"]
        if not (a["low_bp"]>b["low_bp"] and a["high_bp"]>b["high_bp"]):
            return
        signatures.add((a["low_bp"],a["high_bp"],b["low_bp"],b["high_bp"]))
    if len(signatures)!=1:
        return
    v=eligible[0]
    cards["曲线形态"]=dict(tenor="曲线形态",direction=1,conflict=False,conditions=[],
        observations=[dict(subject=v["source_subject"],predicate=v["core"],quote=v["core"],
            citations=v["citations"],judgment_id=v["judgment_id"],role="主判断",direction=1,
            action=None,target=None,number_roles=["条件利差区间"])])
