"""Offline checks for binding claims and portfolio references to evidence."""
from ground_views import adjudicate, share_portfolio


def extract(quote, core):
    spans = {"P1S1": quote}
    candidate = dict(subject="概括标签", core=core, scope="国内利率债",
                     evidence=[dict(id="P1S1",quote=quote)])
    return adjudicate("example", spans, [dict(candidate=candidate)])


def main():
    core = "后续可能还有下行空间，若短端行情延续，长端利率或也下行"
    _, rows = extract("1年国债目前在1.18%，"+core,core)
    assert rows[0]["source_subject"] == "1年国债"
    for sentence in ("美国10年国债后续可能下行", "短端位置建议配置存单或信用债",
                     "上周五10年国债收于1.8%", "期货方面建议关注长端利率"):
        assert not extract(sentence,sentence)[1]
    assert not extract("10Y短期预计在区间内震荡","10Y短期一定上涨")[1]
    text="组合久期建议维持中性，待条件满足后再加久期"
    _, views=extract(text,text)
    f=dict(terms="故组合久期建议维持中性",legs=[["标的","组合久期"]],
           kind="组合久期",evidence=[dict(id="P1S1",page=1,quote=text)])
    assert share_portfolio(views,[f])[0]["judgment_ref"] == views[0]["judgment_id"]


if __name__ == "__main__":
    main()
