"""Refresh the dashboard's end-of-day ChinaBond Treasury curve from the MOF table."""

import argparse
import json
import math
import os
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SOURCE_URL = "https://yield.chinabond.com.cn/cbweb-czb-web/czb/moreInfo?locale=cn_ZH&nameType=1"
SOURCE_LABEL = "中债国债到期收益率曲线（财政部专栏）"
TENORS = {"2年": "1-3Y", "7年": "5-7Y", "10年": "10Y", "30年": "30Y"}
BEIJING = timezone(timedelta(hours=8))
OUTPUT = Path(__file__).resolve().parents[1] / "dashboard_data.json"


class Tables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.stack.append({"rows": [], "row": None, "cell": None})
        elif self.stack and tag == "tr":
            self.stack[-1]["row"] = []
        elif self.stack and tag in ("td", "th") and self.stack[-1]["row"] is not None:
            self.stack[-1]["cell"] = []

    def handle_data(self, data):
        if self.stack and self.stack[-1]["cell"] is not None:
            self.stack[-1]["cell"].append(data)

    def handle_endtag(self, tag):
        if not self.stack:
            return
        table = self.stack[-1]
        if tag in ("td", "th") and table["cell"] is not None:
            cell = unicodedata.normalize("NFKC", "".join(table["cell"]))
            table["row"].append(re.sub(r"\s+", "", cell))
            table["cell"] = None
        elif tag == "tr" and table["row"] is not None:
            table["rows"].append(table["row"])
            table["row"] = None
        elif tag == "table":
            self.tables.append(self.stack.pop()["rows"])


def parse_curve(body, today):
    if "中国国债收益率曲线" not in body:
        raise ValueError("Missing the China Treasury curve title")
    parser = Tables()
    parser.feed(body)
    matches = [
        (rows[index + 1:], len(row))
        for rows in parser.tables for index, row in enumerate(rows)
        if row[:3] == ["日期", "期限", "当日(%)"]
    ]
    if len(matches) != 1:
        raise ValueError("Could not identify a unique daily yield table")
    rows, width = matches[0]
    days = {}
    current = None
    for row in rows:
        if len(row) == width:
            if row[0]:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row[0]):
                    raise ValueError("Unexpected trade date format")
                current = date.fromisoformat(row[0])
                days.setdefault(current, {})
            row = row[1:]
        if current is None or len(row) != width - 1:
            raise ValueError("Unexpected daily yield row")
        key = TENORS.get(row[0])
        if key is None:
            continue
        raw = row[1].removesuffix("%").replace("−", "-")
        if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", raw):
            raise ValueError(f"Missing or invalid yield for {key}")
        value = float(raw)
        if not math.isfinite(value) or not -100 < value < 100 or key in days[current]:
            raise ValueError(f"Invalid or repeated yield for {key}")
        days[current][key] = value
    if not days:
        raise ValueError("Missing trade date")
    latest = max(days)
    if latest > today or set(days[latest]) != set(TENORS.values()):
        raise ValueError("Latest curve has a future date or missing tenor")
    return latest, days[latest]


def read_curve():
    request = Request(SOURCE_URL, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
    with urlopen(request, timeout=35) as response:
        body = response.read()
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = body.decode("gb18030")
    return parse_curve(text, datetime.now(BEIJING).date())


def update_data(data, snapshot):
    trade_date, yields = snapshot
    saved = data.get("benchmark_as_of")
    if saved and trade_date < date.fromisoformat(saved):
        print(f"Ignoring older official curve ({trade_date}); retaining {saved}.")
        return False
    fields = {
        "benchmark_history": {**data.get("benchmark_history", {}), trade_date.isoformat(): yields},
        "benchmark_yields": yields,
        "benchmark_as_of": trade_date.isoformat(),
        "benchmark_note": SOURCE_LABEL,
        "benchmark_source_url": SOURCE_URL,
    }
    if all(data.get(key) == value for key, value in fields.items()):
        return False
    data.update(fields)
    return True


def historical_curves(end, days):
    start = end - timedelta(days=days - 1)
    query = urlencode(dict(zblx="yz", gjqx="2,7,10,30", startTime=start, endTime=end,
                           locale="cn_ZH", qxmc=1))
    url = SOURCE_URL.split("moreInfo")[0] + "czbQueryYz?" + query
    with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60) as response:
        series = json.load(response)
    history = {}
    for line in series:
        tenor = TENORS[line["ycDefName"]]
        for stamp, value in line["seriesData"]:
            day = datetime.fromtimestamp(stamp / 1000, BEIJING).date()
            if not start <= day <= end or not isinstance(value, (int, float)) or not -100 < value < 100:
                raise ValueError("Invalid historical curve observation")
            row = history.setdefault(day.isoformat(), {})
            if tenor in row:
                raise ValueError("Duplicate historical observation")
            row[tenor] = value
    if not history or any(set(row) != set(TENORS.values()) for row in history.values()):
        raise ValueError("Incomplete historical curves")
    if (date.fromisoformat(min(history)) - start).days > 10 or max(history) != end.isoformat():
        raise ValueError("Historical response does not cover requested period")
    return history


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--history-days", type=int, choices=range(90, 184), metavar="90..183")
    args = parser.parse_args()
    snapshot = read_curve()
    data = json.loads(OUTPUT.read_text(encoding="utf-8-sig"))
    history = historical_curves(snapshot[0], args.history_days) if args.history_days else {}
    changed = any(data.get("benchmark_history", {}).get(k) != v for k, v in history.items() if k != snapshot[0].isoformat())
    data.setdefault("benchmark_history", {}).update({k: v for k, v in history.items() if k != snapshot[0].isoformat()})
    if not update_data(data, snapshot) and not changed:
        print("Official benchmark unchanged.")
        return
    with NamedTemporaryFile(dir=OUTPUT.parent, mode="w", encoding="utf-8", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, OUTPUT)
    finally:
        temporary.unlink(missing_ok=True)
    print("BENCHMARK_RESULT=" + json.dumps({
        "date": data["benchmark_as_of"], "values": data["benchmark_yields"]
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
