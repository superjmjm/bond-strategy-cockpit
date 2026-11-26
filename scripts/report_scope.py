"""Limit report intake to the cockpit's covered weekly research series."""

from datetime import datetime
import re

BROKERS = (
    "华泰证券", "华西证券", "申万宏源证券", "浙商证券",
    "兴业证券", "华创证券", "天风证券", "财通证券", "国金证券", "招商证券", "国泰海通证券", "广发证券",
)

MANUAL_BROKER = "中信证券"
_ALIASES = {
    "财通": "财通证券", "国金": "国金证券", "招商": "招商证券", "广发": "广发证券",
    **dict.fromkeys(("国泰海通", "国泰君安", "国泰君安证券", "海通", "海通证券"), "国泰海通证券"),
    "中信": MANUAL_BROKER, MANUAL_BROKER: MANUAL_BROKER,
    "华泰": "华泰证券",
    "华西": "华西证券",
    "申万宏源": "申万宏源证券",
    "申万宏源研究": "申万宏源证券",
    "上海申银万国证券研究所": "申万宏源证券",
    "浙商": "浙商证券",
    "兴业": "兴业证券",
    "华创": "华创证券",
    "天风": "天风证券",
}
_COMPANY_SUFFIX = re.compile(r"(?:股份有限公司|有限责任公司|有限公司)$")
_WEEKLY_SERIES = re.compile(
    r"(?:中国国债|国债|利率债|利率市场|利率|债券市场|债券|债市|固收市场|固收|固定收益)"
    r"(?:策略|研究|市场|专题)?(?:研究)?周(?:度)?(?:报|策略|观察|跟踪|回顾)"
    r"|(?:国债|利率债|利率|债市)(?:策略|研究)?周度"
)
_EXCLUDED = ("科创", "信用", "转债", "REIT", "美债", "债券ETF", "债券基金周度", "私募EB")


def covered_broker(name):
    name = _COMPANY_SUFFIX.sub("", name.strip())
    return name if name in BROKERS else _ALIASES.get(name)


def weekly_broker(title):
    """Return the covered broker for a qualifying listing or archive filename."""
    title = re.sub(r"^\d+-", "", title.strip())
    publisher, separator, subject = title.partition("-")
    if not separator:
        return None
    broker = covered_broker(publisher)
    if broker not in BROKERS or not _WEEKLY_SERIES.search(subject):
        return None
    search_subject = re.sub(r"\s+", "", subject).upper()
    if any(term in search_subject for term in _EXCLUDED):
        return None
    return broker

def manual_identity(filename):
    if not filename.lower().endswith(".pdf") or any(c in filename for c in "/\\"):
        return None
    publisher, _, subject = filename[:-4].partition("-")
    if covered_broker(publisher) != MANUAL_BROKER:
        return None
    for pattern in (r"(?P<day>\d{8}|\d{6})-(?P<title>.+)", r"(?P<title>.+)-(?P<day>\d{8}|\d{6})"):
        match = re.fullmatch(pattern, subject)
        if match:
            stamp = match["day"]
            day = datetime.strptime(stamp, "%Y%m%d" if len(stamp) == 8 else "%y%m%d").date()
            return dict(broker=MANUAL_BROKER, series="周报", title=match["title"], report_date=day.isoformat())
    return None
