import copy
import unittest
from reader import finish, numeric_values, project
from source import context
from decimal import Decimal

P="P1-S1"
Q="P2-S1"
R="P2-S2"
INDEX={"report_id":"unit","paragraphs":[dict(pid=p,page=page,text=t,heading="策略") for p,page,t in [
    (P,1,"1.70%下方，围绕1.68%盘整。"),(Q,2,"政策窗口保持观望。"),
    (R,2,"短期45–50bp，供给高峰后40–45bp。")]]}
CANDS={"C1":{"pids":[P,Q]},"C2":{"pids":[R]}}
def measure(v,p,role,window="短期",unit="%"):
    return dict(value=v,unit=unit,role=role,window=window,pids=[p])
def card(bucket="10Y国债",p=P,c="C1"):
    return dict(bucket=bucket,candidate_ids=[c],evidence_pids=[p],direction=0,vote_eligible=True,
                numbers=[],scenarios=[],drivers=[],horizon="短期",target_low=None,target_high=None)
def out(cards):
    return finish({},INDEX,CANDS,dict(cards=cards,report_category="利率策略周报",
                  unmerged_clues=[],rejected=[]),{P,Q,R})
class TestGuardrails(unittest.TestCase):
    def test_unique_card_and_sources(self):
        c=card();c["evidence_pids"]=[P,Q]
        result=out([c]);self.assertEqual([p["page"] for p in result["cards"][0]["evidence"]],[1,2])
        self.assertEqual([p["text"] for p in result["cards"][0]["evidence"]],[r["text"] for r in INDEX["paragraphs"][:2]])
        self.assertEqual(len(result["unmerged_clues"]),1)
    def test_duplicate_isolated(self):
        c=card();other=card("曲线形态",R,"C2")
        result=out([c,copy.deepcopy(c),other]);self.assertEqual([c["bucket"] for c in result["cards"]],["曲线形态"])
        self.assertEqual(len(result["s3"]),2)
    def test_invalid_anchor(self):
        result=out([card(p="P99-S1"),card("曲线形态",R,"C2")])
        self.assertEqual(len(result["cards"]),1);self.assertTrue(result["unmerged_clues"][0]["evidence"])
    def test_role_and_numeric(self):
        c=card();c.update(target_low=1.68,target_high=1.7,numbers=[measure(1.68,P,"震荡中枢"),measure(1.7,P,"阻力/支撑关口"),measure(8.88,P,"预测单点目标")])
        got=out([c])["cards"][0];self.assertIsNone(got["target_low"]);self.assertIsNone(got["numbers"][2]["value"])
        self.assertEqual(got["numbers"][1]["value"],1.7)
        self.assertNotIn(Decimal("1.68"),numeric_values("11.68%"))
        self.assertEqual(numeric_values("４５–５０bp"),{Decimal(45),Decimal(50)})
    def test_curve_and_unvoted(self):
        c=card("曲线形态",R,"C2");c["direction"]=1
        c["numbers"]=[measure(value,R,role,window,"bp") for window,lo,hi in [("短期",45,50),("高峰后",40,45)] for role,value in [("预测区间下限",lo),("预测区间上限",hi)]]
        b=card("整体久期/组合");b["vote_eligible"]=False
        got=out([b,c])["cards"];self.assertEqual(sum(x["vote_eligible"] for x in got),1)
        self.assertEqual([n["value"] for n in got[1]["numbers"]],[45,50,40,45])
    def test_adjustment_per_bucket(self):
        a=card("30Y超长债");a["direction"]=-1
        b=card("中端(5-7Y)");b["direction"]=1
        self.assertEqual([c["direction"] for c in out([a,b])["cards"]],[-1,1])
    def test_context_and_targets(self):
        self.assertEqual([x["pid"] for x in context(INDEX,[Q])],[P,Q,R])
        c=card();c.update(target_low=1.68,target_high=1.7,numbers=[measure(1.68,P,"预测区间下限"),measure(1.7,P,"预测区间上限")])
        self.assertEqual(project(c),(1.68,1.7))
if __name__=="__main__": unittest.main()

