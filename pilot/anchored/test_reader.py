import json
import unittest
from unittest.mock import patch

from jsonschema import ValidationError, validate
from contract import SLOTS, STAGE2
from reader import MODEL, finish, open_cache, read_index, targets
from source import index_pdf

ROWS = [dict(pid=f"P1-S{i}",page=1,text=t,heading="债市策略") for i,t in enumerate(
    ("4/8项指标触发，3只基金。","预测1.60–1.70％。","短期45至50BP，随后40–45基点。","观点以外的线索。"),1)]
IDX = dict(report_id="fixture",paragraphs=ROWS,pages=[])
P,Q,R,T = [row["pid"] for row in ROWS]

def slots(**values):
    return dict(report_category="利率策略周报",**(dict.fromkeys(SLOTS)|values))

def card(**values):
    base = dict(raw_tenor="10年",instrument_scope="国债",account_note="",vote_eligible=True,
                direction=0,action="观望",horizon="短期",target_low=None,target_high=None,
                target_unit=None,target_note="",cond="",risk="",logic="维持观望",drivers=[],evidence_pids=[P])
    return base|values

class ContractTests(unittest.TestCase):
    def test_all_required_null_slots_no_legacy_keys(self):
        validate(slots(),STAGE2)
        validate(slots(cgb_10y=card()),STAGE2)
        for payload in [slots(cards=[]), slots(rejected=[]), slots(unmerged_clues=[]),
                        slots(cgb_10y=card(candidate_ids=[])),slots(cgb_10y=card(scenarios=[])),
                        slots(cgb_10y=card(numbers=[])),slots(cgb_10y=card(bucket="曲线形态"))]:
            with self.subTest(payload=payload),self.assertRaises(ValidationError):
                validate(payload,STAGE2)
        missing=slots(); del missing["curve"]
        with self.assertRaises(ValidationError): validate(missing,STAGE2)

    def test_union_and_clue_difference(self):
        d=dict(layer="资金面",impact="中性",phrase="资金",pids=[Q,"foreign"])
        c=card(drivers=[d],evidence_pids=[P,P,"foreign"])
        cands=[{"pids":[P,Q,T,T]},{"pids":[T,"foreign"]}]
        out=finish({},IDX,cands,slots(cgb_10y=c))
        self.assertEqual(out["cards"][0]["evidence_pids"],[P,Q])
        self.assertEqual(out["cards"][0]["drivers"][0]["pids"],[Q])
        self.assertEqual([e["evidence_pids"] for e in out["unmerged_clues"]],[[T]])

    def test_target_warning_keeps_card_and_note(self):
        v=card(target_low=4,target_high=4,target_unit="%",target_note="4/8项触发")
        out=finish({},IDX,[],slots(cgb_10y=v))
        c=out["cards"][0]
        self.assertEqual((c["target_low"],c["target_high"],c["target_unit"],c["target_note"]),(None,None,None,"4/8项触发"))
        self.assertEqual(len(out["numeric_warnings"]),1)

    def test_target_range_and_unit(self):
        for bucket,low,high,unit,source,ok in [
            ("10Y国债",1.6,1.7,"%","1.60–1.70％",True),
            ("10Y国债",1.6,1.7,"%","1.60%-1.70%",True),
            ("10Y国债",4,4,"%","4/8项，8%占比",False),
            ("10Y国债",4,4,"%","14%",False),("10Y国债",4,4,"%","4BP；1.8%",False),
            ("10Y国债",0.49,0.49,"%","0.49%",False),("10Y国债",4.51,4.51,"%","4.51%",False),
            ("10Y国债",1.6,1.7,"bp","1.6–1.7bp",False),("10Y国债",1.7,1.6,"%","1.6–1.7%",False),
            ("曲线形态",-50,150,"bp","−50到150基点",True),("曲线形态",45,50,"bp","45–50BP",True),
            ("曲线形态",45,50,"bp","45和50%",False),("曲线形态",None,50,"bp","50bp",False),
            ("曲线形态",-51,150,"bp","-51到150bp",False),("整体久期/组合",1,1,"%","1%",False)]:
            with self.subTest(bucket=bucket,source=source):
                self.assertEqual(targets(dict(bucket=bucket,target_low=low,target_high=high,target_unit=unit),[{"text":source}]),ok)

    def test_adjacent_source_and_invalid_primary_anchor(self):
        c=card(target_low=1.6,target_high=1.7,target_unit="%")
        out=finish({},IDX,[],slots(cgb_10y=c,ultra_30y=card(evidence_pids=["foreign"])))
        self.assertEqual(len(out["cards"]),1)
        self.assertEqual(out["cards"][0]["target_low"],1.6)
        self.assertEqual(len(out["s3"]),1)

    def test_full_text_and_replay_empty_funding(self):
        calls=[]
        def ask(model,stage,prompt,payload,schema):
            self.assertEqual(model,"gpt-4.1-2025-04-14")
            self.assertEqual(payload["paragraphs"],ROWS)
            calls.append(stage)
            return ({"candidates":[]} if stage=="stage1" else slots()|{"report_category":"资金流动性周报"}),{}
        db=open_cache(":memory:")
        try:
            result=read_index(IDX,{},db,invoke=ask)
            self.assertEqual(result["output"]["cards"],[])
            self.assertEqual(result,read_index(IDX,{},db,offline=True))
            self.assertEqual(calls,["stage1","stage2"])
        finally: db.close()

    def test_normal_paragraphs_section_paths_and_physical_pages(self):
        class Page(dict):
            def __init__(self,raw): self.raw=raw
            def extract_text(self,visitor_text):
                visitor_text(self.raw,None,None,None,10)
                return self.raw
        class PDF:
            outline=[]
            pages=[Page("一、市场回顾\n（一）机构行为\n基金久期上升。\n换手也上升！\n\n另一自然段。"),
                   Page("次页续文。\n二、投资策略\n建议观望。")]
        with patch("source.PdfReader",return_value=PDF()):
            rows=index_pdf(b"pdf")["paragraphs"]
        self.assertEqual(rows[2]["text"],"基金久期上升。\n换手也上升！")
        self.assertEqual(rows[2]["heading"],"一、市场回顾 / （一）机构行为")
        self.assertEqual(rows[4]["pid"],"P2-S1")
        self.assertEqual(rows[4]["heading"],rows[2]["heading"])
        self.assertEqual(rows[-1]["heading"],"二、投资策略")

if __name__=="__main__": unittest.main()
