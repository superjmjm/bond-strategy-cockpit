"""Private pilot: source-grounded strategies and candidate disposition receipts."""
import hashlib
import re

from evidence_cards import distinct, encode, normalize, resolve

OUTSIDE = re.compile(r"美债|美国国债|美元债|信用债|城投债|企业债|转债|存单")
HISTORY = re.compile(r"此前|上期|上周建议|曾建议|前期建议|回顾")
RENEW = re.compile(r"(?:仍|继续|维持|重申).{0,6}(?:组合|建议|配置策略)")
BOND = r"(?:\d+(?:[-–至]\d+)?年(?:期)?(?:国债|政金债|地方债)|长久期地方债)"
LEG = re.compile(BOND)
SWITCH = re.compile(r"(?:将|把)(" + BOND + r")(小幅|适度|逐步)?(?:向|转向)(" + BOND + r"(?:[、和及]" + BOND + r")*)切换")
PAIR = re.compile(r"(?:建议|宜)(规避|减配)(超长端|长端|短端)(?:品种)?[，,](?:建议)?以(\d+[-–至]\d+年期)(?:品种)?为主(?:参与|配置)")
LEVEL = re.compile(r"(" + BOND + r")(\d+(?:\.\d+)?%附近[^。；]{0,12}(?:阻力|支撑)[^。；]{0,8})[，,]((?:如果|若)[^。；]{1,32}(?:止盈|增配|减仓))")
CHASE = re.compile(r"(" + BOND + r")收益率([^。；]{1,110}?[，,](?:继续)?追(?:多|涨)性价比不高)")
ENTRY = re.compile(r"((?:优先|建议)(?:把握|关注)(?:调整|回调)后的配置机会[，,](?:不宜|避免)[^。；]{0,8}追涨)")
MARKET = re.compile(r"(?:维持|重申)债市(?:震荡偏多|震荡偏空)判断")
FOREIGN = re.compile(r"美债|美国国债|美元债|海外债")
NEGATED = re.compile(r"(?:不再|取消|撤回|否定)(?:维持|建议|此前|原有|原|该)?(?:组合|配置|建议)")


def cite(key, text):
    return dict(id=key, page=int(key.split("S")[0][1:]), quote=text)


def strategy_records(digest, spans):
    records = {}

    def add(key, kind, legs, terms, evidence, clause):
        signature = encode([digest, kind, legs, normalize(terms)])
        uid = hashlib.sha256(signature.encode()).hexdigest()
        record = records.setdefault(uid, dict(judgment_id=uid, kind=kind, legs=legs,
                                               terms=terms, evidence=[], matched=[]))
        record["evidence"] = distinct(record["evidence"] + evidence)
        record["matched"] = distinct(record["matched"] + [dict(id=key, clause=clause)])

    for key, s in spans.items():
        pg, idx = key.split("S")
        previous = [(k, spans[k]) for i in range(max(1, int(idx)-8), int(idx))
                    if (k := pg+"S"+str(i)) in spans]
        nxt = pg+"S"+str(int(idx)+1)
        following = spans.get(nxt, "")
        if FOREIGN.search(s) or NEGATED.search(s):
            continue
        renewal = RENEW.search(s) or RENEW.match(following)
        if HISTORY.search(s) and (not renewal or NEGATED.search(following)):
            continue
        ev = [cite(key, s)]
        if (m := SWITCH.search(s)) and (not HISTORY.search(s) or renewal):
            legs = [["调出", m[1]]] + [["调入", b] for b in LEG.findall(m[3])]
            extension = [cite(nxt, following)] if RENEW.match(following) else []
            add(key, "组合切换", legs, m[0], ev+extension, m[0])
        if OUTSIDE.search(s):
            continue
        if m := PAIR.search(s):
            add(key, "期限取舍", [[m[1], m[2]], ["优先参与", m[3]]], m[0], ev, m[0])
        if m := LEVEL.search(s):
            add(key, "条件操作", [["标的", m[1]]], m[0], ev, m[0])
        if m := CHASE.search(s):
            add(key, "追涨判断", [["标的", m[1]]], m[0], ev, m[0])
        if m := ENTRY.search(s):
            anchor = next(((k, x) for k, x in reversed(previous) if MARKET.search(x)), None)
            if anchor and not any(OUTSIDE.search(x) for k, x in previous if int(k.split("S")[1]) > int(anchor[0].split("S")[1])):
                terms = MARKET.search(anchor[1])[0] + "；" + m[0]
                add(key, "债市配置", [["标的", "债市"]], terms, [cite(*anchor)]+ev, m[0])
    return list(records.values())


def reconcile(digest, spans, raw, parsed, cards, portfolio):
    strategies = strategy_records(digest, spans)
    indexed = []
    for rec in strategies:
        for match in rec.pop("matched"):
            indexed.append((match["id"], match["clause"], rec["judgment_id"]))
    for card in cards.values():
        for obs in (card or {}).get("observations", []):
            quote = obs.get("quote") or obs["sentence"]
            uid = hashlib.sha256(encode([digest, card["tenor"], obs.get("subject"), obs.get("predicate"),
                                         quote]).encode()).hexdigest()
            obs["judgment_id"] = uid
            indexed.extend((c["id"], quote, uid) for c in (obs.get("citations") or [obs]))
    kept = []
    references = {}
    for f in portfolio:
        if OUTSIDE.search(f.get("subject", "") or f.get("scope", "")):
            continue
        key, quote = f.get("id"), f.get("quote", "")
        same = [uid for k, clause, uid in indexed if key == k and clause and
                (clause in quote or quote in clause) and quote]
        if same:
            for uid in set(same):
                references.setdefault(uid, []).append(cite(key, spans[key]))
            continue
        kept.append(f)
    kept += [dict(judgment_ref=uid, citations=distinct(evidence))
             for uid, evidence in sorted(references.items())
             if not any(rec["judgment_id"] == uid for rec in strategies)]
    kept += strategies
    audit = []
    for bucket, items in raw.items():
        for i, item in enumerate(items):
            key, q = item["id"], item["quote"]
            grounded = resolve(spans, item)
            matches = sorted({uid for k, clause, uid in indexed if k == key and q and
                              (q in clause or clause in q) and grounded and
                              item["subject"] in clause and item["predicate"] in clause})
            if matches:
                reason = "已关联原文判断"
            elif not grounded:
                reason = "引文或主谓无法定位，或属于此前观点"
            elif OUTSIDE.search(item["subject"]):
                reason = "品种超出国内利率债范围"
            elif any(f.get("id")==key and f.get("quote")==q for f in kept):
                reason = "已保留组合证据"
            elif any(c["id"]==key and f.get("quote")==q for card in cards.values() if card
                     for f in card["conditions"] for c in (f.get("citations") or [f])):
                reason = "已保留条件"
            else:
                reason = "未形成独立判断"
                for skipped in parsed["rejected"]:
                    f = skipped["item"]
                    if skipped["tenor"]==bucket and f.get("quote")==q:
                        reason = skipped["reason"]
                        break
            audit.append(dict(bucket=bucket, ordinal=i, candidate=item, disposition=reason,
                              judgment_ids=matches))
    for rec in strategies:
        rec["candidate_links"] = [[a["bucket"], a["ordinal"]] for a in audit
                                  if rec["judgment_id"] in a["judgment_ids"]]
        rec["origin"] = "候选与原文核对" if rec["candidate_links"] else "原文补读"
    return kept, audit
