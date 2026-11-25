import unittest
from reader import finish
from test_reader import IDX, P, Q, R, card, slots

def cleaned(texts, pids):
    rows = [dict(row,text=text) for row,text in zip(IDX["paragraphs"],texts)]
    review = slots(ultra_30y=card(evidence_pids=pids))
    return finish({}, dict(IDX,paragraphs=rows), [], review)["cards"][0]["evidence_pids"]

class CoverTests(unittest.TestCase):
    def test_keep_mixed_paragraph(self):
        self.assertEqual(cleaned(["分析师：张三","证券研究报告\n建议加仓超长债。","报告日期：2026年11月23日"],[P,Q,R]),[Q])
    def test_all_metadata_or_only_one(self):
        self.assertEqual(cleaned(["证券研究报告","免责声明"],[P,Q]),[P,Q])
        self.assertEqual(cleaned(["证券研究报告"],[P]),[P])
