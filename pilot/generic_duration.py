"""Keep explicit, conditional long-end allocations outside tenor voting."""
import re

CONDITIONAL = re.compile(
    r"(?:回调(?:过程中|时|后)?|调整(?:时|后)|逢(?:回调|调整)|收益率上行时)"
    r"(?:可|建议|宜)(?:适度|择机|逐步)?(?:把握|关注|增配|配置)"
    r"(?:长端(?:利率债|国债)?(?:配置(?:窗口|机会|价值))?)"
)
EXCLUDED = re.compile(r"美债|美国国债|美元债|信用债|地方债|国开债|政金债|(?:10|30|十|三十)(?:年|[yY])|此前(?:指出|建议)|上周(?:建议|提示)|历史回顾|不建议|不宜|不应|不再|避免|并非")
MARKET = re.compile(r"短期(?:内)?债市(?:预计|或|仍|将|可能)*延续区间震荡格局")

def unspecified_long_end(spans):
    groups={}
    for key,sentence in spans.items():
        found=CONDITIONAL.search(sentence)
        if not found or EXCLUDED.search(sentence):
            continue
        page,idx=key.split("S")
        nearby=[(page+"S"+str(i),spans.get(page+"S"+str(i),""))
                for i in range(max(1,int(idx)-2),int(idx))]
        if any(re.search(r"美债|美国国债|美元债",s) for _,s in nearby):
            continue
        quote=found[0]
        group=groups.setdefault(quote,dict(role="泛长端配置",scope="长端（期限未细分）",
                                          predicate=quote,citations=[],market_context=[]))
        group["citations"].append(dict(id=key,page=int(page[1:]),quote=sentence))
        for sid,s in nearby:
            if MARKET.search(s):
                group["market_context"].append(dict(id=sid,page=int(page[1:]),quote=s))
    return list(groups.values())
