"""Isolated report-level cards. Quotes and conditions survive deduplication."""
from datetime import date, timedelta
import json
import re
import sqlite3
import time
import unicodedata
import uuid


TENORS = ("短端(1-3Y)", "中端(5-7Y)", "10Y国债", "30Y超长债", "曲线形态")
SUBJECTS = (
    r"(?:[1一][-至~到][3三](?:年|y)|短端|短期)(?:期)?(?:国债|利率债)|短债",
    r"(?:[5五][-至~到][7七](?:年|y)|中端)(?:期)?(?:国债|利率债)|[57五七](?:年|y)(?:期)?国债",
    r"(?<![\d-])(?:10|十)(?:年|y)(?:期)?国债",
    r"(?<![\d-])(?:30|三十)(?:年|y)(?:期)?国债|超长(?:期|端)?(?:国债|利率债|债)",
    r"(?:30|三十)(?:年|y)?[-至~到](?:10|十)(?:年|y)?(?:国债)?(?:期限)?利差|(?:国债)?(?:收益率)?曲线",
)
FOREIGN = re.compile(r"地方债|政金债|国开|农发|口行|信用债|存单|lpr")
RANGE = re.compile(r"(\d+(?:\.\d+)?)%?[-~至到](\d+(?:\.\d+)?)%")
SPREAD = re.compile(r"30(?:y|年)?[-至~到]10(?:y|年)?")
SIGNALS = (
    (r"区间震荡|区间运行|平台期|震荡运行|下行空间有限|阻力增大|需关注.{0,14}扰动", 0),
    (r"补充配置盘力量|配置需求(?:增加|改善)|看好|偏多|看多", 1),
    (r"偏空|看空", -1),
)
ACTIONS = ((r"波段","区间波段"),(r"(?:可|建议|考虑)(?:逐步|适度|分批)?止盈","止盈"),(r"逢(?:高|低|调整|回调).{0,6}(?:布局|介入|增配|加仓)","择机布局"),
           (r"维持久期","维持久期"),(r"降低久期|缩短久期","降低久期"),(r"拉长久期|延长久期","拉长久期"))


def normalize(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).lower().translate(str.maketrans("–—－～", "---~"))


def encode(item):
    return json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def distinct(items):
    return [json.loads(s) for s in sorted({encode(x) for x in items})]


def collect_evidence(facts):
    grouped={}
    for fact in facts:
        meaning={k:v for k,v in fact.items() if k not in ("id","page","sentence","citations")}
        key=encode(meaning)
        cites=fact.get("citations",[]) or [dict(id=fact["id"],page=fact["page"],sentence=fact.get("sentence",fact["quote"]))]
        grouped.setdefault(key,[]).extend(cites)
    return [dict(**json.loads(key),citations=distinct(grouped[key])) for key in sorted(grouped)]


def is_subject(tenor, subject):
    text = normalize(subject)
    if FOREIGN.search(text):
        return False
    if tenor != TENORS[4] and SPREAD.search(text):
        return False
    return bool(re.search(SUBJECTS[TENORS.index(tenor)], text))


def interpret(tenor, fact):
    phrase = normalize(fact["predicate"])
    clause = normalize(fact["quote"])
    subject = normalize(fact["subject"])
    # Do not accept a fragment of a cross-instrument spread as an outright bond.
    if not is_subject(tenor, subject):
        return None
    if tenor != TENORS[4] and any(SPREAD.search(clause[max(0,m.start()-7):m.end()]) for m in re.finditer(re.escape(subject),clause)):
        return None
    if re.search(r"不(?:会|宜|建议|宜再|再|应)?(?:走陡|走平|压缩|加仓|减仓|看多|看好|看空|拉长)|并非|不可简单",phrase):
        return None
    if tenor == TENORS[4]:
        if re.search(r"走陡|陡峭化|走阔",phrase):
            direction = -1
        elif re.search(r"压缩|收窄|走平|下行空间",phrase):
            direction = 1
        else:
            direction = None
    else:
        direction = next((n for pat,n in SIGNALS if re.search(pat,phrase)),None)
    action = next((name for pat,name in ACTIONS if re.search(pat,phrase)),None)
    # Trading intent is independent of a forecast. A forecast of flattening has no action.
    limits = None
    bounds = RANGE.search(phrase)
    if bounds and re.search(r"区间|震荡",phrase) and tenor != TENORS[4]:
        low, high = map(float,bounds.groups())
        if low <= high:
            limits = [low,high]
    roles=[]
    if limits:
        roles.append("预测区间")
    if re.search(r"\d+(?:\.\d+)?%.{0,8}(?:阻力|支撑|关口)",phrase):
        roles.append("阻力或支撑")
    if action and re.search(r"接近|触及|上方|下方|突破|跌破",phrase):
        roles.append("操作触发条件")
    return dict(direction=direction,action=action,target=limits,number_roles=roles)


def resolve(spans, item):
    key = item["id"]
    if key not in spans:
        return None
    raw = spans[key]
    quote = re.sub(r"\s+", "", item["quote"])
    subject = re.sub(r"\s+", "", item["subject"])
    predicate = re.sub(r"\s+", "", item["predicate"])
    if not quote or not subject or not predicate or quote not in raw or subject not in quote or predicate not in quote:
        return None
    if re.search(r"前一周提示|曾建议|上期曾|此前曾提示",raw) and not re.search(r"重申|继续建议|仍建议",raw):
        return None
    return dict(id=key,page=int(key.split("S")[0][1:]),sentence=raw,quote=quote,subject=subject,predicate=predicate,role=item["role"])


def make_card(tenor, evidence):
    observations, context, rejected = [], [], []
    for fact in collect_evidence(evidence):
        if fact["role"] == "补充条件" or (is_subject(tenor,fact["subject"]) and re.match(r"^(?:但)?(?:如果|若|倘若|一旦)",fact["quote"])):
            context.append(fact)
            continue
        reading = interpret(tenor,fact)
        if reading is None or all(reading[k] is None for k in ("action","direction","target")):
            rejected.append(fact)
        else:
            observations.append(dict(**fact,**reading))
    if not observations:
        return None, rejected + context
    directions = {x["direction"] for x in observations if x["direction"] is not None}
    targets = {tuple(x["target"]) for x in observations if x["target"] is not None}
    # Alternative scenarios and incompatible targets remain visible without a consensus vote.
    conflict = len(directions)>1 or len(targets)>1
    return dict(tenor=tenor,observations=observations,conditions=context,conflict=conflict,
                direction=next(iter(directions)) if len(directions)==1 and not conflict else None), rejected


def connect(path):
    db = sqlite3.connect(path, timeout=30)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS report (
            id TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE, broker TEXT NOT NULL, day TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS source (id TEXT PRIMARY KEY, report_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS slot (
            report_id TEXT NOT NULL, tenor TEXT NOT NULL, PRIMARY KEY (report_id, tenor)
        );
        CREATE TABLE IF NOT EXISTS reading (
            report_id TEXT NOT NULL, tenor TEXT NOT NULL, body TEXT NOT NULL,
            PRIMARY KEY (report_id,tenor,body)
        );
        CREATE TABLE IF NOT EXISTS extraction (
            digest TEXT NOT NULL, version TEXT NOT NULL, owner TEXT,
            expires REAL, result TEXT, PRIMARY KEY(digest,version)
        );
    """)
    return db


def cached_extract(db, digest, version, extractor, lease_seconds=1200):
    """Only the lease owner can install its result. A restart can reuse committed output."""
    owner=uuid.uuid4().hex
    with db:
        db.execute("BEGIN IMMEDIATE")
        record=db.execute("SELECT result,expires FROM extraction WHERE digest=? AND version=?",
                          (digest,version)).fetchone()
        if record and record[0] is not None:
            return json.loads(record[0]),"cached"
        if record and record[1]>time.time():
            raise BlockingIOError("Another reader is processing this PDF and extractor version")
        db.execute("INSERT OR REPLACE INTO extraction VALUES(?,?,?,?,NULL)",
                   (digest,version,owner,time.time()+lease_seconds))
    try:
        value=extractor()
        with db:
            updated=db.execute("UPDATE extraction SET result=?,owner=NULL,expires=NULL WHERE digest=? AND version=? AND owner=?",
                               (encode(value),digest,version,owner)).rowcount
            if updated!=1:
                raise RuntimeError("Extraction lease superseded")
        return value,"fresh"
    except BaseException:
        with db:
            db.execute("DELETE FROM extraction WHERE digest=? AND version=? AND owner=?", (digest,version,owner))
        raise


def ingest(db, source, digest, broker, day, cards, portfolio):
    """One transaction includes empty slots; divergent re-reads cannot silently replace old results."""
    if set(cards) != set(TENORS):
        raise ValueError("Incomplete report extraction")
    if not re.fullmatch(r"[0-9a-f]{64}",digest):
        raise ValueError("Invalid PDF fingerprint")
    with db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT OR IGNORE INTO report VALUES(?,?,?,?)",(digest,digest,broker,day))
        db.execute("INSERT OR IGNORE INTO source VALUES(?,?)",(source,digest))
        if db.execute("SELECT report_id FROM source WHERE id=?",(source,)).fetchone()!=(digest,):
            raise ValueError("Source changed bytes; review edition identity")
        if db.execute("SELECT broker,day FROM report WHERE id=?",(digest,)).fetchone()!=(broker,day):
            raise ValueError("Report identity conflict")
        fields = dict(cards,组合层=distinct(portfolio))
        db.executemany("INSERT OR IGNORE INTO slot VALUES(?,?)", [(digest,t) for t in fields])
        db.executemany("INSERT OR IGNORE INTO reading VALUES(?,?,?)",
                       [(digest,t,encode(value)) for t,value in fields.items()])


def snapshot(db):
    cards, disputed, portfolios = [], [], []
    query = """SELECT r.id,r.broker,r.day,s.tenor,count(*),min(v.body)
               FROM report r JOIN slot s ON r.id=s.report_id
               JOIN reading v USING(report_id,tenor)
               GROUP BY r.id,s.tenor ORDER BY r.day,r.broker,r.id,s.tenor"""
    for rid,broker,day,tenor,variants,body in db.execute(query):
        source=db.execute("SELECT min(id) FROM source WHERE report_id=?",(rid,)).fetchone()[0]
        key = dict(report_id=rid,broker=broker,date=day,tenor=tenor,pdf_url="https://drive.google.com/file/d/"+source+"/view")
        if variants>1 and tenor!="组合层":
            records=[json.loads(v) for v, in db.execute("SELECT body FROM reading WHERE report_id=? AND tenor=?",(rid,tenor))]
            if all(isinstance(v,dict) for v in records):
                signatures=[]
                for v in records:
                    signatures.append(encode({**v,**{k:distinct([{a:b for a,b in f.items() if a not in ("id","page","citations")} for f in v[k]]) for k in ("observations","conditions")}}))
                if len(set(signatures))==1:
                    merged=dict(records[0])
                    for k in ("observations","conditions"):
                        merged[k]=collect_evidence(f for v in records for f in v[k])
                    body=encode(merged)
                    variants=1
        if variants>1:
            disputed.append(dict(**key,variants=variants))
        elif tenor=="组合层":
            if body!="[]":
                portfolios.append(dict(**key,evidence=json.loads(body)))
        elif body!="null":
            cards.append({**json.loads(body),**key})
    groups={}
    for card in cards+disputed:
        if card["tenor"]=="组合层":
            continue
        day=date.fromisoformat(card["date"])
        monday=day+timedelta(days=1 if day.weekday()==6 else -day.weekday())
        key=(monday.isoformat(),card["broker"],card["tenor"])
        groups.setdefault(key,set()).add(card.get("direction"))
    votes=[dict(week=wk,broker=broker,tenor=t,direction=next(iter(values)))
           for (wk,broker,t),values in sorted(groups.items()) if len(values)==1 and None not in values]
    return dict(cards=cards,disputed=disputed,portfolio=portfolios,votes=votes)
