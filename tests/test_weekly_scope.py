"""Regression cases from NXNY titles, archived PDFs and weekend observation weeks."""
from datetime import date
from pathlib import Path
import sys
from unittest import TestCase
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "zip_a")]
from anchored_batch import identity
from build_snapshot import infer_week_mon, validate_and_dedup_views
from report_io import name_for, parse_listing, scan_listing
from report_scope import BROKERS, covered_broker, manual_identity, weekly_broker


SAT = "兴业证券-债券周报：月末大额缴款，仍看央行投放-261121"
SUN = "兴业证券-债券周报：看股做债时刻-261122"
MAP = "广发证券-周策略图谱：跨年行情的五大利多因素思考-261121"
XLS = "国泰海通证券-债券市场策略周报：年内地方债剩余发行金额0.93万亿左右-261117"
GOLD = "国金证券-固定收益策略报告：“换锚”后，资金为何趋紧？-261115"


def row(rid, title, pages, icon="pdf", uploaded="20261122"):
    return (f'<li><div class="rlist"><a title="{title}" href="/report/view_{rid}.html">'
            f'<img src="/images/hz/{icon}.gif">{title}</a></div>'
            f'<span>页数：{pages}</span><span>上传时间：{uploaded}</span></li>')


class WeeklyScope(TestCase):
    def test_series(self):
        samples = (
            ("华泰证券-固收利率周报：本轮股债跷跷板评估-261025", "利率周报"),
            ("国联民生证券-债券策略周报：继续关注超长债机会-261123", "债券策略周报"),
            ("申万宏源证券-流动性观察周报-261121", "流动性观察周报"),
            ("申万宏源证券-机构行为观察周报：纯债基金久期与理财-261121", "机构行为观察周报"),
            ("浙商证券-债市策略思考：债市跨年行情还有下半场吗-261121", "债市策略思考"),
            ("浙商证券-债市跨年行情还有下半场吗——债市策略思考-261121", "债市策略思考"),
            (SUN, "债券周报"),
            ("华创证券-【债券周报】一揽子政策落地，会打破当前债市格局吗？——债券周报-261011", "债券周报"),
            ("天风证券-固收周度点评：30年能否接力？-261108", "固收周度点评"),
            ("财通证券-利率｜技术面叠加机构行为，能给出什么判断？-261122", "利率周报"),
            ("财通证券-固收定期报告：机构行为-261122", "固收定期报告"),
            (GOLD, "固定收益策略报告"),
            ("招商证券-利率策略周报：长债修复后，关注抢跑节奏-261122", "利率策略周报"),
            ("国泰海通证券-市场策略周报：年末“期-现”波动的新特征和应对-260104", "市场策略周报"),
            ("国泰海通证券-机构行为周度跟踪—理财与纯债基金久期-261122", "机构行为周度跟踪"),
            ("广发证券-债市周思录：跨年行情-261122", "债市周思录"), (MAP, "周策略图谱"),
        )
        self.assertEqual(len(BROKERS), 12)
        for title, series in samples:
            with self.subTest(title=title):
                broker = title.partition("-")[0]
                self.assertEqual(weekly_broker(title), (broker, series))
                self.assertEqual(weekly_broker("123-" + name_for(title) + ".pdf"), (broker, series))
                expected = (broker, series) if title == MAP else None
                self.assertEqual(weekly_broker(title, channel="hc"), expected)

    def test_rejections(self):
        for title in (
            XLS + ".xlsx", GOLD + ".xlsx.pdf",
            "国泰海通证券-市场策略周报：银行理财规模高增，夯实信用债配置需求-261027",
            "华创证券-【债券周报】存单周报：利率自律机制会议或将召开-261122",
            "华创证券-【债券周报】每周高频跟踪：外贸吞吐量延续回落——债券周报-261122",
            "华创证券-【债券周报】10月金融数据解读——债券周报-261114",
            "浙商证券-债市量化周度跟踪：从择时与轮动信号共振看股债配置思考-261101",
            "国泰海通证券-机构行为周度跟踪：海外理财-261121",
            "招商证券-利率策略周报：纯债基金久期-261121",
            "国金证券-利率策略周报：债市-261121", "华西证券-债券策略周报：跨年-261121",
            "中信证券-利率策略周报：跨年-261121", "广发证券-A股策略：周策略图谱-261121",
        ):
            with self.subTest(title=title):
                self.assertIsNone(weekly_broker(title))

    def test_aliases_manual_and_identity(self):
        for names, broker in [
            (("国联民生", "民生证券", "民生", "国联证券"), "国联民生证券"),
            (("申万宏源", "申万宏源研究", "上海申银万国证券研究所"), "申万宏源证券"),
            (("国泰海通", "国泰君安", "国君", "海通证券", "海通"), "国泰海通证券"),
        ]:
            for name in names:
                self.assertEqual(covered_broker(name), broker)
        self.assertEqual(manual_identity("每周市场策略聚焦20261123—关注跨年行情.pdf")["broker"], "中信证券")
        filename = "123-" + MAP + ".pdf"
        old = [{"source_file": filename, "broker": "广发证券", "report_title": "跨年行情"}]
        self.assertEqual(identity(filename, old)["series"], "周策略图谱")
        with self.assertRaises(ValueError):
            identity("华西证券-周报-261121.pdf", old)

    def test_industrial_pages_and_archive(self):
        pair = ("兴业证券", "债券周报")
        for n in (14, 15):
            self.assertIsNone(weekly_broker(SAT, pages=n))
        self.assertEqual(weekly_broker(SAT, pages=16), pair)
        self.assertEqual(identity("123-" + SAT + ".pdf", [], {"title": SAT, "pages": 16})["series"], pair[1])
        self.assertEqual(weekly_broker(SUN, pages=21), pair)
        self.assertEqual(weekly_broker(SUN + ".pdf"), pair)
        self.assertIsNone(weekly_broker(SUN.replace("261122", "261121") + ".pdf"))
        self.assertIsNone(weekly_broker(SAT.replace("261121", "261122") + ".pdf"))


class Listing(TestCase):
    def test_pdf_and_page_markers(self):
        body = (row("1", XLS, 18, "xlsx") + row("2", GOLD, 0) + row("3", GOLD, "")
                + row("4", GOLD, 15, "PDF") + row("5", GOLD, 15).replace('src=', 'alt=')
                + row("6", GOLD+".docx", 15) + row("7", GOLD, 15).replace("</li>", "格式：xls</li>"))
        self.assertEqual(parse_listing(body), {"4": (GOLD, date(2026, 11, 15), date(2026, 11, 22), 15)})
        self.assertEqual(parse_listing(row("1", XLS, 0, "xlsx")), {})
        with self.assertRaises(ValueError):
            parse_listing("<html>Login</html>")

    def test_scan_both_channels_even_when_bond_limit_is_full(self):
        base = "https://www.nxny.com/stype_"
        pages = {
            "qh/": row("0", XLS, 0, "xlsx") + '<a href="/stype_qh_p2/">2</a>',
            "qh_p2/": row("1", SAT, 14) + row("2", SUN, 21) + row("3", GOLD, 15),
            "hc/": row("4", GOLD, 15) + '<a href="/stype_hc_p2/">2</a>',
            "hc_p2/": row("5", MAP, 20),
        }
        def response(url, **kwargs):
            return Mock(content=pages[url.removeprefix(base)].encode())
        with patch("report_io.requests.get", side_effect=response) as get:
            reports, scanned, issue = scan_listing(date(2026, 11, 23), set(), {}, 2, {}, days=10)
        self.assertEqual([x[0] for x in reports], ["2", "5"])
        self.assertEqual([x[2:] for x in reports], [(21, "qh"), (20, "hc")])
        self.assertEqual([c.args[0] for c in get.call_args_list], [base + p for p in pages])
        self.assertEqual((scanned, issue), (4, None))


class Snapshots(TestCase):
    def test_weekend_and_year_boundary(self):
        for day in ("2027-01-02", "2027-01-03", "2027-01-04", "2027-01-08"):
            self.assertEqual(infer_week_mon(day), "2027-01-04")
        self.assertEqual(infer_week_mon("2022-12-31"), "2023-01-02")

    def test_series_priority_complement_and_order(self):
        def card(id_, day, tenor, series, vote=True):
            return dict(report_id=id_, broker="国泰海通证券", report_date=day, tenor=tenor, series=series,
                        vote_eligible=vote, direction=1, evidence=[], account_note="")
        main, sub = "市场策略周报", "机构行为周度跟踪"
        rows = [card("a", "2026-11-21", "10Y国债", main), card("b", "2026-11-23", "10Y国债", sub),
                card("c", "2026-11-22", "30Y超长债", sub), card("d", "2026-11-23", "30Y超长债", sub, False),
                card("e", "2026-11-23", "30Y超长债", sub), card("f", "2026-11-14", "10Y国债", main)]
        for order in (rows, reversed(rows)):
            result, errors = validate_and_dedup_views(list(order))
            self.assertEqual(errors, [])
            self.assertEqual([v["report_id"] for v in result], ["f", "a", "e"])
