"""Regression probes with synthetic wording, run without model access."""
from conditional_views import extend_views

def resolve(spans, selection):
    audit=[]
    for keys in selection:
        evidence=[dict(id=k,quote=spans[k]) for k in keys]
        audit.append(dict(candidate=dict(core="",subject="",evidence=evidence),disposition="未保留"))
    views=[]
    extend_views("pdf",spans,audit,views,[])
    return views

def main():
    source="原有信用债和存单组合，建议部分换入7年政金债、长久期地方债寻找配置机会。"
    spans={"P2S2":source,"P5S3":"我们"+source}
    rows=resolve(spans,[list(spans)])
    assert len(rows)==1 and rows[0]["instruments"]==["7年政金债","长久期地方债"]
    assert rows[0]["clauses"][0]["from_context"]=="原有信用债和存单组合"
    for bad in ("不"+source[source.index("建议"):],"原有国债组合，不建议转入地方债。","去年对原有组合当时建议转入地方债。"):
        assert not resolve({"P1S1":bad},[["P1S1"]])

    first="若财政发力延迟，可跟踪7年国债。"
    next_="在此条件下，可关注回调时的配置机会。"
    spans={"P3S1":first,"P3S2":next_}
    rows=resolve(spans,[["P3S2"]])
    assert len(rows)==1 and rows[0]["instruments"]==["7年国债"]
    assert [c["quote"] for c in rows[0]["citations"]]==[first,next_.rstrip("。")]
    for altered in ({"P3S1":first.replace("7年国债","7年国债与10年国债"),"P3S2":next_},
                    {"P2S1":first,"P3S2":next_},{"P3S1":first,"P3S3":next_}):
        assert not resolve(altered,[[list(altered)[-1]]])
    spans={"P1S1":"对于负债期限较长的账户。","P1S2":"这类账户可关注20年国债。"}
    rows=resolve(spans,[["P1S2"]])
    assert rows[0]["clauses"][0]["context_citation"]["id"]=="P1S1"
    assert not resolve({"P1S2":spans["P1S2"]},[["P1S2"]])

    spans={"P1S1":"未来三周关注流动性，周二操作已落地，若融资需求回升，警惕债市止盈压力增强。",
           "P1S2":"未来三周关注流动性，周二操作已落地，若融资需求回落，警惕债市止盈压力增强。"}
    rows=resolve(spans,[list(spans)])
    assert len(rows)==2 and all(v["clauses"][0]["horizon"] for v in rows)
    assert not resolve({"P1S1":"周二操作已落地，债市止盈压力增强。"},[["P1S1"]])

if __name__=="__main__":
    main()
