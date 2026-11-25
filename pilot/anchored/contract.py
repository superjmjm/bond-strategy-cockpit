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
ROLES=["预测区间下限","预测区间上限","预测单点目标","震荡中枢","阻力/支撑关口","触发入场条件","相对变动bp"]
LAYERS=["基本面","政策面","资金面","机构行为","供给面","估值情绪"]
candidate=obj(core=TEXT,instruments=TEXT,action=TEXT,condition=TEXT,account=TEXT,
              numbers=array(obj(text=TEXT,use=TEXT)),pids=PIDS)
STAGE1=obj(candidates=array(candidate))
measure=obj(value=NUMBER,unit=TEXT,role=enum(*ROLES),window=TEXT,pids=PIDS)
driver=obj(layer=enum(*LAYERS),impact=enum("利多","利空","中性","不确定"),phrase=TEXT,pids=PIDS)
scenario=obj(window=TEXT,account=TEXT,condition=TEXT,forecast=TEXT,action=TEXT,pids=PIDS)
card=obj(bucket=enum(*BUCKETS),raw_tenor=TEXT,instrument_scope=TEXT,account_note=TEXT,
         vote_eligible=BOOL,direction={"type":"integer","enum":[-2,-1,0,1,2]},
         action=TEXT,horizon=TEXT,target_low=NUMBER,target_high=NUMBER,numbers=array(measure),
         cond=TEXT,risk=TEXT,logic=TEXT,drivers=array(driver),scenarios=array(scenario),
         candidate_ids=array(TEXT),evidence_pids=PIDS)
clue=obj(candidate_ids=array(TEXT),reason=TEXT,evidence_pids=PIDS)
reject=obj(candidate_ids=array(TEXT),reason=TEXT)
STAGE2=obj(report_category=enum(*CATEGORIES),cards=array(card),unmerged_clues=array(clue),rejected=array(reject))

