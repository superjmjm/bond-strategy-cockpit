"""Adjudicate cached paragraph proposals against physical-page source spans."""
import hashlib
import re

from evidence_cards import encode

SUBJECT = re.compile(
    r"(?:(?<![\d.])\d{1,2}(?:[-–至]\d{1,2})?(?:年(?:期)?|[Yy])(?:及以内)?(?:国债|地方债|政金债|利率债|利率)?"
    r"(?:和\d+(?:年|Y)(?:国债)?利差)?|超长端利率|超长债|长端利率|短端利率|"
    r"长端交易敞口|债券组合|组合久期|平衡配置|债市|短期行情|期限利差)")
OUTSIDE = re.compile(r"存单|信用|美债|美国|日本国债|德国国债|美元债|海外利率|美股|转债|期债|期货|合约|(?<![A-Z])TL|美联储")
INTENT = re.compile(r"预计|有望|大概率|可能|或(?:也|仍|更|将|有)|仍有.{0,10}空间|"
                    r"短时间不会|偏多|偏空|看好|关注|建议|不宜|观望|等待|"
                    r"配置机会|维持|值博率|空间有限|加仓|减仓|交易空间|性价比|后续|接下来")
HISTORY = re.compile(r"202[0-5]年|历史上|彼时|上周[一二三四五]|周[一二三四五]|"
                     r"本周.{0,14}(?:上行|下行|先下后上)|全周|截至")
METHOD = re.compile(r"例如|举例|均线|技术分析|BOLL|RSI|MACD|通常|一般而言")
CURRENT = re.compile(r"接下来|后续|往后看|预计|预期|有望|短时间|目前|仍有.{0,10}空间")
CONTEXT_ONLY = re.compile(r"风险提示|DR00[17]|拆借成本|逆回购操作窗口")


def literal_parts(core):
    # An independent trailing credit allocation can be removed. Keep a mixed
    # comparison, condition or switch together for review.
    for m in re.finditer("[，；]", core):
        tail = core[m.end():]
        if re.match(r"信用(?:债|供需)", tail) and not re.search(r"相对利率债|切换|如果|取决于", core[:m.start()]):
            return core[:m.start()]
    return core


def adjudicate(digest, spans, items):
    records, audit = {}, []
    for entry in items:
        c = entry["candidate"]
        core = literal_parts(c["core"]).strip()
        cites = [dict(id=e["id"],page=int(e["id"].split("S")[0][1:]),quote=e["quote"])
                 for e in c["evidence"] if e["quote"] and e["quote"] in spans.get(e["id"],"")
                 and core and core in e["quote"]]
        reason = "判断无法在有效引文中连续定位"
        if cites:
            reason = "未识别当前国内利率债策略"
            # The anchor must be in the same verified quote. A shorthand such
            # as 10Y is kept literal, never promoted to an outright Treasury.
            first_clause = re.split("[，；]",core,1)[0]
            prefix = cites[0]["quote"].split(core)[0][-90:]
            anchor = SUBJECT.search(first_clause)
            previous = list(SUBJECT.finditer(prefix))
            if not anchor and previous and not OUTSIDE.search(prefix[previous[-1].end():]):
                anchor = previous[-1]
            anchor = anchor or SUBJECT.search(core)
            local = spans[cites[0]["id"]]
            pg,num=cites[0]["id"].split("S")
            nearby=[spans.get(pg+"S"+str(i),"") for i in range(max(1,int(num)-3),int(num))]
            market_prefix = local.partition(core)[0].rsplit("，",1)[-1]
            contrast = (nearby and re.match(r"但也需要注意|不过上述",local)
                        and re.search(r"存单|短端信用",nearby[-1]))
            futures_curve = ("曲线" in core and any("国债期货" in t for t in nearby)
                             and any("曲线策略" in t for t in nearby+[local]))
            if futures_curve or re.search(r"国债期货|期货策略|期货方面",local) or any(
                token in core for token in ("期货合约","期债")):
                reason = "期货策略范围"
                audit.append(dict(candidate=c,disposition=reason))
                continue
            if OUTSIDE.search(core) or contrast or CONTEXT_ONLY.search(core) or (OUTSIDE.search(prefix) and not SUBJECT.search(first_clause)):
                reason = "范围外品种或仅背景"
            elif METHOD.search(core) or (HISTORY.search(core) and not CURRENT.search(core)):
                reason = "历史事实或方法说明"
            elif anchor and INTENT.search(core) and not OUTSIDE.search(market_prefix[-12:]):
                subject = anchor[0]
                uid = hashlib.sha256(encode([digest,core]).encode()).hexdigest()
                rec = records.setdefault(uid,dict(judgment_id=uid,subject=subject,source_subject=subject,
                    display_label=subject,model_label=c["subject"],core=core,scope=c["scope"],citations=[]))
                if re.fullmatch(r"\d+(?:[Yy]|年)(?:利率)?",subject):
                    rec["scope"]="待核"
                    rec["display_label"]=subject+"（券种待核）"
                for evidence in cites:
                    if evidence not in rec["citations"]:
                        rec["citations"].append(evidence)
                audit.append(dict(candidate=c,disposition="待核品种" if rec["scope"]=="待核" else "保留",
                                  judgment_id=uid))
                continue
        audit.append(dict(candidate=c,disposition=reason))
    # Merge nested restatements only with a shared source span and one
    # unambiguous fuller judgment. Preserve its conditions and all citations.
    remap={}
    for uid,a in sorted(records.items(),key=lambda kv:len(kv[1]["core"])):
        keys={c["id"] for c in a["citations"]}
        fuller=[b for b in records.values() if b["source_subject"]==a["source_subject"]
                and b["scope"]==a["scope"] and a["core"]!=b["core"] and a["core"] in b["core"]
                and any(c["id"] in keys for c in b["citations"])]
        if len(fuller)==1:
            b=fuller[0]
            remap[uid]=b["judgment_id"]
            b["citations"].extend(c for c in a["citations"] if c not in b["citations"])
    for a in audit:
        while a.get("judgment_id") in remap:
            a["judgment_id"]=remap[a["judgment_id"]]
    return audit,[r for uid,r in records.items() if uid not in remap]


def share_portfolio(views, portfolio):
    output = []
    for row in portfolio:
        terms = re.sub(r"^(?:因此|故|所以)", "", row.get("terms", ""))
        keys = {e["id"] for e in row.get("evidence", [])}
        matches = [v for v in views if terms and (terms in v["core"] or v["core"] in terms)
                   and any(c["id"] in keys for c in v["citations"])]
        if len(matches) == 1:
            match = matches[0]
            output.append(dict(judgment_ref=match["judgment_id"],kind=row["kind"]))
            match["portfolio_legs"]=row["legs"]
            for e in row["evidence"]:
                if e not in match["citations"]:
                    match["citations"].append(e)
        else:
            output.append(row)
    return output
