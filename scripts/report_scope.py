"""Limit report intake to the cockpit's covered weekly research series."""

from datetime import datetime
import re

MANUAL_BROKER = "中信证券"
_SERIES = {
    "华泰证券": (("利率周报", r"^(?:固收)?利率(?:策略)?周报(?:[：:(-]|$)"),),
    "国联民生证券": (("债券策略周报", r"^债券策略周报(?:[：:(-]|$)"),),
    "申万宏源证券": (
        ("流动性观察周报", r"^流动性观察周报(?:[：:(-]|$)"),
        ("机构行为观察周报", r"^机构行为观察周报(?:[：:(-]|$)"),
    ),
    "浙商证券": (("债市策略思考", r"^债市策略思考(?:[：:(-]|$)|(?:——|--|—)债市策略思考$"),),
    "兴业证券": (("债券周报", r"^债券周报[：:]"),),
    "华创证券": (("债券周报", r"^债券周报[：:]|^【债券周报】(?!(?:存单|信用|可转债|转债|每周高频|政策双周报|\d+月)).*(?:——|--|—)债券周报$"),),
    "天风证券": (("固收周度点评", r"^固收周度点评[：:]"),),
    "财通证券": (("利率周报", r"^利率[｜|]"), ("固收定期报告", r"^固收定期报告[：:]")),
    "国金证券": (("固定收益策略报告", r"^固定收益策略报告[：:]"),),
    "招商证券": (("利率策略周报", r"^利率策略周报[：:]"),),
    "国泰海通证券": (
        ("市场策略周报", r"^市场策略周报[：:\-—]"),
        ("机构行为周度跟踪", r"^机构行为周度跟踪[：:\-—]"),
    ),
    "广发证券": (("债市周思录", r"^债市周思录[：:]"), ("周策略图谱", r"^周策略图谱[：:]")),
}
BROKERS = tuple(_SERIES)
_ALIASES = {
    **dict.fromkeys(("国联民生", "民生证券", "民生", "国联证券"), "国联民生证券"),
    **dict.fromkeys(("国泰海通", "国泰君安", "国泰君安证券", "国君", "海通证券", "海通"), "国泰海通证券"),
    **dict.fromkeys(("申万宏源", "申万宏源研究", "上海申银万国证券研究所"), "申万宏源证券"),
    **{broker.removesuffix("证券"): broker for broker in BROKERS},
    "中信": MANUAL_BROKER, MANUAL_BROKER: MANUAL_BROKER,
}
_COMPANY_SUFFIX = re.compile(r"(?:股份有限公司|有限责任公司|有限公司)$")
_INSTITUTION_SERIES = {"机构行为观察周报", "机构行为周度跟踪"}
_COMMON_EXCLUSIONS = ("转债", "REIT", "美债", "美元债", "海外", "私募EB")
_STRATEGY_EXCLUSIONS = _COMMON_EXCLUSIONS + (
    "科创", "信用", "择券", "城投", "二永", "债券ETF", "债券基金", "纯债基金",
)
_NON_PDF_SUFFIX = re.compile(
    r"\.(xlsx|xlsm|xlsb|xls|csv|tsv|zip|rar|7z|docx|docm|doc|pptx|ppt|txt|html|htm|xml|json|ods|odt|wps)(?![a-z0-9])",
    re.IGNORECASE,
)


def non_pdf_suffix(name):
    match = _NON_PDF_SUFFIX.search(name)
    return match[1].lower() if match else None


def covered_broker(name):
    name = _COMPANY_SUFFIX.sub("", name.strip())
    return name if name in BROKERS else _ALIASES.get(name)


def weekly_broker(title, *, pages=None, channel="qh"):
    """Classify an NXNY listing title or an archived filename."""
    if non_pdf_suffix(title) or channel not in {"qh", "hc"}:
        return None
    title = re.sub(r"^\d+-", "", title.strip())
    title = re.sub(r"\.pdf$", "", title, flags=re.I)
    publisher, _, subject = title.partition("-")
    stamp = re.search(r"-(\d{6})$", subject)
    if stamp:
        subject = subject[:stamp.start()]
    subject = subject.strip()
    broker = covered_broker(publisher)
    for series, pattern in _SERIES.get(broker, ()):
        if channel == "hc" and (broker, series) != ("广发证券", "周策略图谱"):
            continue
        if not re.search(pattern, subject):
            continue
        exclusions = _COMMON_EXCLUSIONS if series in _INSTITUTION_SERIES else _STRATEGY_EXCLUSIONS
        normalized = re.sub(r"\s+", "", subject).upper()
        if any(word in normalized for word in exclusions):
            return None
        if broker == "兴业证券":
            if pages is not None:
                if pages < 16:
                    return None
            else:
                if re.search(r"央行投放|大额缴款|税期将至", normalized):
                    return None
                if stamp:
                    try:
                        day = datetime.strptime(stamp[1], "%y%m%d").date()
                    except ValueError:
                        return None
                    if day.weekday() == 5:
                        return None
        return broker, series
    return None


def manual_identity(filename):
    if not filename.lower().endswith(".pdf") or non_pdf_suffix(filename) or any(c in filename for c in "/\\"):
        return None
    stem = filename[:-4].strip()
    series = "每周市场策略聚焦"
    native = re.fullmatch(r"每周市场策略聚焦\s*(\d{8})\s*[-‐‑‒–—―－:：]+\s*(.+)", stem)
    if native and native[2].strip():
        day = datetime.strptime(native[1], "%Y%m%d").date()
        return dict(broker=MANUAL_BROKER, series=series, title=native[2].strip(), report_date=day.isoformat())
    publisher, _, subject = stem.partition("-")
    if covered_broker(publisher) != MANUAL_BROKER:
        return None
    for pattern in (r"(?P<day>\d{8}|\d{6})-(?P<title>.+)", r"(?P<title>.+)-(?P<day>\d{8}|\d{6})"):
        match = re.fullmatch(pattern, subject)
        if match:
            stamp = match["day"]
            day = datetime.strptime(stamp, "%Y%m%d" if len(stamp) == 8 else "%y%m%d").date()
            return dict(broker=MANUAL_BROKER, series="周报", title=match["title"], report_date=day.isoformat())
    return None
