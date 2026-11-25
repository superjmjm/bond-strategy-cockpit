"""Model contracts for the isolated two-pass reader (no production imports)."""
def obj(**fields):
    return {"type":"object","properties":fields,"required":list(fields),"additionalProperties":False}

def array(item):
    return {"type":"array","items":item}

def enum(*values):
    return {"type":"string","enum":list(values)}

TEXT={"type":"string"}
NUMBER={"type":["number","null"]}
BOOL={"type":"boolean"}
PIDS=array(TEXT)
BUCKETS=["短端(1-3Y)","中端(5-7Y)","10Y国债","30Y超长债","曲线形态","整体久期/组合"]
CATEGORIES=["利率策略周报","资金流动性周报","其他"]
SLOTS = dict(zip(("short_1_3y", "mid_5_7y", "cgb_10y", "ultra_30y", "curve", "overall_portfolio"), BUCKETS))
LAYERS = ["基本面","政策面","资金面","机构行为","供给面","估值情绪"]
candidate = obj(core=TEXT, instruments=TEXT, action=TEXT, condition=TEXT, account=TEXT,
                numbers=array(obj(text=TEXT, use=TEXT)), pids=PIDS)
STAGE1 = obj(candidates=array(candidate))
driver = obj(layer=enum(*LAYERS), impact=enum("利多","利空","中性","不确定"), phrase=TEXT, pids=PIDS)
card = obj(raw_tenor=TEXT, instrument_scope=TEXT, account_note=TEXT, vote_eligible=BOOL,
           direction={"type":"integer","enum":[-2,-1,0,1,2]}, action=TEXT, horizon=TEXT,
           target_low=NUMBER, target_high=NUMBER,
           target_unit={"anyOf":[enum("%","bp"),{"type":"null"}]}, target_note=TEXT,
           cond=TEXT, risk=TEXT, logic=TEXT, drivers=array(driver), evidence_pids=PIDS)
slot = {"anyOf":[{"$ref":"#/$defs/card"},{"type":"null"}]}
STAGE2 = obj(report_category=enum(*CATEGORIES), **{key:slot for key in SLOTS})
STAGE2["$defs"] = {"card":card}
