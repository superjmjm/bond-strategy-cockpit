"""Resolve conditional author proposals against individually verified PDF spans."""
import hashlib
import re

from evidence_cards import encode
from scoped_strategy import instrument_index

INSTRUMENT = re.compile(r"(?:长久期|超长期|[0-9]+(?:[-–至][0-9]+)?(?:年期?|[yY]))?(?:政金债|地方债|国债|利率债|国开债|农发债|浮息债)")
EXCLUDED = re.compile(r"美债|美国|日本|德国|海外|美元|期货|期债|期权|转债|合约")
OUTSIDE = re.compile(r"信用|存单|二永|城投|商金债")
SWITCH = re.compile(r"(?:建议|可考虑|可|宜)(?:将)?(?:部分|逐步|适度)?(?:转向|转入|调入|换入|切换至)")
CONDITION = re.compile(r"(?:如果|倘若|若是|若|一旦)")
FORWARD = re.compile(r"未来|下周|后续|短期|接下来")
RISK = re.compile(r"提防|警惕|可能|或将|需防范")
HISTORICAL = re.compile(r"(?:上周|周[一二三四五]|此前|昨日|前期).*(?:已|缩量|落地|上行|下行)")
ACCOUNT = re.compile(r"(?:对于|针对|适合)?[^，。；]{0,10}(?:账户|配置盘)")
RATE = re.compile(r"(?<![\d.])\d{1,2}(?:年期?|[yY])国债")
BAND = re.compile(r"\d+(?:\.\d+)?%?(?:下方|以下|上方|以上|左右|附近)|围绕\d")
WAIT = re.compile(r"多看少动|观望为主|暂缓入场|暂不加仓")
ALLOCATION = re.compile(r"可(?:以)?关注|适合配置|可配置|可考虑")
WINDOW = re.compile(r"窗口|之前|阶段|期间|在.{0,16}(?:时|后)")
PROFIT = re.compile(r"止盈|兑现收益")
STRATEGY = re.compile(r"持仓|仓位|组合")
ENUMERATION = re.compile(r"^(?:第[一二三四]|[一二三四]是)[、，是]?")

def citation(key, quote):
    return dict(id=key, page=int(key[1:].split("S")[0]), quote=quote)

def previous(key, spans):
    page, number = key.split("S")
    other = page+"S"+str(int(number)-1)
    return other, spans.get(other,"")

def literal_evidence(candidate, spans):
    for e in candidate["evidence"]:
        key, q = e["id"], e["quote"]
        if not q or q not in spans.get(key,"") or EXCLUDED.search(q):
            continue
        yield key, q.rstrip("。；")

def relations(key, q, spans):
    if EXCLUDED.search(spans[key]) or re.search(r"风险提示|历史上|当时(?:建议|认为)|曾(?:预计|建议|预测)",spans[key]):
        return
    switch = SWITCH.search(q)
    if switch:
        before, after = q[:switch.start()].rstrip("，"), q[switch.end():]
        dest = re.split(r"寻找|以期|以获取|以博取|以增强", after)[0]
        names = [m[0] for m in INSTRUMENT.finditer(dest)]
        if before and re.search(r"组合|持仓|换出|转出|从",before) and names and not re.search("不宜|不建议|勿|避免",q[:switch.end()]):
            # Source basket and ambiguous issuers remain context. Only explicit
            # eligible destinations are indexed, and the rotation has no outright vote.
            yield ("相对换仓", q.removeprefix("我们"), "组合", dict(from_context=before,to_context=after,
                   in_scope_legs=[n for n in names if not n.endswith("浮息债")],
                   unspecified_issuer=[n for n in names if n.endswith("浮息债")]))
        return
    if OUTSIDE.search(q):
        return
    clauses=q.split("，")
    conditional=CONDITION.search(q)
    if conditional and any(HISTORICAL.search(x) for x in clauses) and FORWARD.search(q) and RISK.search(q[conditional.end():]) and re.search(r"止盈|配置|加仓|减仓|债市|长债|久期",q[conditional.end():]):
        future=q[conditional.start():]
        horizon=next((x for x in clauses if FORWARD.match(x)), "")
        yield ("条件风险",future,"债市",dict(horizon=horizon,background=[x for x in clauses if HISTORICAL.search(x)]))
        return
    rates=RATE.search(q)
    if rates and WINDOW.search(q):
        if BAND.search(q) and re.search("盘整|波动|震荡",q) and re.search(r"阶段|之前|期间",q):
            start=re.search(r"(?:在|若|当)[^，。；]{1,35}(?:之前|阶段|期间)",q)
            if start:
                yield ("条件区间",q[start.start():],rates[0],{})
        if WAIT.search(q) and re.search(r"突破.{0,6}(?:难|阻力)|(?:难以|不易).{0,6}突破",q):
            yield ("窗口操作",q.removeprefix("但是从观察因素看，"),rates[0],{})
    pk,p=previous(key,spans)
    referent=re.match(r"^(?:这类账户|该类账户|上述账户|其可|其宜|该券|该期限|这一期限|在此条件下|届时)",q)
    if not referent and ACCOUNT.search(q) and ALLOCATION.search(q) and INSTRUMENT.search(q):
        yield ("账户配置",q,INSTRUMENT.search(q)[0],{})
    elif referent and p and len(p)<140 and not (OUTSIDE.search(p) or EXCLUDED.search(p)):
        # An explicit backward reference may use one immediately preceding
        # span. Multiple antecedents or a contrast require human review.
        accounts=ACCOUNT.findall(p)
        tenors=list(dict.fromkeys(INSTRUMENT.findall(p)))
        if not re.search(r"分别|另一|而对于|但对于|两类|不同账户",p):
            if (referent[0] in ("这类账户","该类账户","上述账户","其可","其宜")
                    and len(accounts)==1 and ALLOCATION.search(q) and INSTRUMENT.search(q)):
                yield ("账户配置",q,INSTRUMENT.search(q)[0],dict(context_citation=citation(pk,p)))
            elif (len(tenors)==1 and (WAIT.search(q) or ALLOCATION.search(q))
                    and (referent[0] in ("该券","该期限","这一期限") or
                         (referent[0] in ("在此条件下","届时") and CONDITION.search(p)))):
                yield ("条件操作",q,tenors[0],dict(context_citation=citation(pk,p)))
    if ENUMERATION.match(q) and PROFIT.search(q) and STRATEGY.search(p) and re.search(r"建议|应当|宜",p) and not (OUTSIDE.search(p) or EXCLUDED.search(p)):
        yield ("持仓细节",q,"组合",dict(parent_citation=citation(pk,p)))
    if re.search(r"利率(?:短期|近期).{0,10}(?:下行|上行).{0,8}空间.{0,4}有限",q) and not re.search(r"资金利率|政策利率|回购利率|贷款利率",q):
        yield ("利率展望",q,"利率",{})
    if "监管" in q and re.search(r"(?:国债|利率债).{0,12}(?:需要|宜|应|谨慎)",q):
        subject=INSTRUMENT.search(q)
        if subject:
            yield ("监管风险",q,subject[0],{})

def scenario_signature(core, details):
    # Compare a repeated lead trigger and risk after stripping only reporting
    # language. Keep polarity, numeric thresholds and the complete alternatives.
    clauses=core.split("，")
    horizon=re.search(r"(?:未来|接下来)([一二两三四五六七八九十0-9-]+)(周|日|月)",details["horizon"])
    if not horizon or not details["background"] or len(clauses)<2:
        return None
    risk=next((x for x in clauses[1:] if RISK.search(x) and not x.startswith(("或","若","如果"))),"")
    if not risk:
        return None
    risk=re.sub(r"^(?:提防|警惕|需防范)","",risk).replace("可能","")
    trigger=CONDITION.sub("",clauses[0],count=1)
    trigger=re.sub(r"^(?:出现|下周|届时)", "",trigger).replace("同步","")
    when=horizon[1].translate(str.maketrans("一二两三四五六七八九","1223456789"))
    return [details["background"],trigger,risk,when.split("-")[-1]+horizon[2]]


def attach_percentage(candidates, spans, source, core, subject, record):
    # A centre printed without % sometimes has an explicit unit in a second
    # cited sentence. Never borrow a percent from a different tenor or value.
    centre=re.search(r"围绕(\d+(?:\.\d+)?)(?![\d.%])(?:左右)?(?:盘整|波动)",core)
    if not centre:
        return
    pattern=re.compile(re.escape(subject)+r"围绕"+re.escape(centre[1])+r"%(?:左右)?(?:盘整|波动)")
    for candidate,_ in candidates:
        for key,q in literal_evidence(candidate,spans):
            page,num=key.split("S")
            current_page,current_num=source.split("S")
            if page!=current_page or abs(int(num)-int(current_num))>1:
                continue
            found=pattern.search(q)
            if found:
                evidence=citation(key,found[0])
                if evidence not in record["citations"]:
                    record["citations"].append(evidence)
                record["centre_quote"]=found[0]
                return


def extend_views(digest, spans, audit, views, legacy):
    candidates=[(row["candidate"],row) for row in audit]
    for row in legacy:
        c=row["candidate"]
        if c["role"]=="主判断":
            candidates.append((dict(evidence=[c]),row))
    for candidate,row in candidates:
        for key,q in literal_evidence(candidate,spans):
            for kind,core,subject,details in relations(key,q,spans):
                cite=citation(key,q)
                parent=details.get("parent_citation")
                context=details.get("context_citation")
                matches=[v for v in views if not context and (core==v["core"] or
                         ((core in v["core"] or v["core"] in core) and
                          any(e["id"]==key for e in v["citations"])))]
                if parent:
                    matches=[v for v in views if v["core"] in parent["quote"] and STRATEGY.search(v["core"])]
                    if len(matches)!=1:
                        continue
                if kind=="条件风险" and not matches:
                    # Summary/body variants can share a record only with a common
                    # background, risk and explicit funding trigger. Each variant
                    # keeps its own forecast horizon and all alternative triggers.
                    signature=scenario_signature(core,details)
                    if signature:
                        details["scenario_group"]=signature
                        matches=[v for v in views if any(x.get("scenario_group")==signature for x in v.get("clauses",[]))]
                if len(matches)>1:
                    continue
                if matches:
                    rec=matches[0]
                else:
                    identity=[digest,kind,core,context and context["quote"]]
                    uid=hashlib.sha256(encode(identity).encode()).hexdigest()
                    rec=next((v for v in views if v["judgment_id"]==uid),None)
                    if rec is None:
                        inst=details.get("in_scope_legs",instrument_index(core))
                        if not inst and INSTRUMENT.fullmatch(subject): inst=[subject]
                        if context and subject not in inst: inst=[subject,*inst]
                        rec=dict(judgment_id=uid,subject=subject,source_subject=subject,display_label=subject,
                                 model_label=candidate.get("subject",subject),core=core,
                                 scope="组合" if kind=="相对换仓" else ("债市整体" if subject in ("债市","利率") else "国内利率债"),
                                 instruments=inst,spread_windows=[],citations=[])
                        views.append(rec)
                for evidence in (parent,context,cite):
                    if evidence and not any(e["id"]==evidence["id"] and evidence["quote"] in e["quote"] for e in rec["citations"]):
                        rec["citations"].append(evidence)
                if kind=="条件区间":
                    attach_percentage(candidates,spans,key,core,subject,rec)
                clause=dict(kind=kind,core=core,source=key,**details)
                if clause not in rec.setdefault("clauses",[]):
                    rec["clauses"].append(clause)
                uid=rec["judgment_id"]
                refs=row.setdefault("author_judgment_ids",[])
                if uid not in refs:
                    refs.append(uid)

    for row in audit:
        refs=row.get("author_judgment_ids",[])
        if refs:
            row["initial_disposition"]=row["disposition"]
            if row.get("judgment_id") and row["judgment_id"] not in refs:
                refs.insert(0,row["judgment_id"])
            row["disposition"]="保留（逐分句核验）"
