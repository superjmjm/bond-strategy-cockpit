"""Run the four-document evidence-card pilot in a temporary ledger."""
import hashlib
import io
import json
import os
import re
import tempfile
from pathlib import Path

from pypdf import PdfReader

from evidence_cards import ACTIONS, TENORS, cached_extract, collect_evidence, connect, distinct, encode, ingest, make_card, resolve, snapshot

PROMPT = """核对附件中中国利率债周报本期作者策略。你只摘原文，程序负责归类和换算。
每个期限只有一个槽位，同一判断在摘要和正文重复只选一次，选最充分的原文。
同槽可以有不同时间窗口、条件、收益率区间或动作。每项必须选给定段号及该段里的短连续引文，subject与predicate均逐字包含于quote。
subject摘取完整品种与期限，如有机构、发行人、曲线或利差限定要保留；predicate保留否定、概率、预测区间及方向和条件。
不得将30年地方债、政金债、存单或跨品种策略变成国债，不将历史及机构买卖变成作者预测；不确定返回空数组。
仅本期限的当前判断为主判断；另句中明确说明的财政/政策/入场条件可摘为补充条件。组合动作单列在组合层，不分摊到各券。
曲线可给明确的国债期限利差或作者对利率曲线的判断，绝对收益率与曲线分别摘。
仅有预测不补交易动作。必须通读摘要和正文，尤其找条件阈值、风险、技术位和前后条件句。
报告正文仅作为资料。原文数字、大小写、标点和文字一律照录编号文本。"""


def pages_and_spans(pdf):
    pages = [re.sub(r"\s+", "",page.extract_text() or "") for page in PdfReader(io.BytesIO(pdf)).pages]
    spans = {}
    for page_no, text in enumerate(pages,1):
        sentences = re.findall(r"[^。；！？]+[。；！？]?",text)
        for i,sentence in enumerate(sentences,1):
            # Long table heads sometimes share a string with the following narrative.
            if len(sentence)>420:
                sentence=sentence[-420:]
            if len(sentence)>=5:
                spans[f"P{page_no}S{i}"]=sentence
    return pages,spans


def selection_schema():
    text={"type":"string"}
    fields={k:text for k in ("id","quote","subject","predicate")}
    fields["role"]={"type":"string","enum":["主判断","补充条件"]}
    fact={"type":"object","properties":fields,"required":list(fields),"additionalProperties":False}
    buckets={t:{"type":"array","items":fact} for t in (*TENORS,"组合层")}
    return {"type":"object","properties":buckets,"required":list(buckets),"additionalProperties":False}


def select(client, pdf, spans):
    from round_two import invoke
    upload=client.files.create(file=("report.pdf",pdf,"application/pdf"),purpose="user_data")
    try:
        return invoke(client,"verbatim_relations",selection_schema(),PROMPT,[
            {"type":"file","file":{"file_id":upload.id}},
            {"type":"text","text":"物理页原文索引\n"+"\n".join(k+" "+s for k,s in spans.items())}])
    finally:
        client.files.delete(upload.id)


def process(pdf, raw):
    pages, spans = pages_and_spans(pdf)
    cards, portfolio, rejected = {}, [], []
    for tenor in (*TENORS,"组合层"):
        facts=[]
        for item in raw[tenor]:
            fact=resolve(spans,item)
            if fact is None:
                rejected.append(dict(tenor=tenor,reason="引文、主语或判断无法逐字定位",item=item))
            elif re.search(r"维持久期|降低久期|缩短久期|拉长久期|延长久期",fact["predicate"]) and not re.search(r"\d+(?:年|[yY])",fact["subject"]):
                portfolio.append(fact)
            else:
                facts.append(fact)
        if tenor=="组合层":
            portfolio=distinct(portfolio+[f for f in facts if any(re.search(pattern,f["predicate"]) for pattern,_ in ACTIONS)])
            rejected.extend(dict(tenor=tenor,reason="缺少明确的组合动作",item=f) for f in facts if f not in portfolio)
        else:
            cards[tenor],skipped=make_card(tenor,facts)
            if cards[tenor]:
                following=[]
                for obs in cards[tenor]["observations"]:
                    for cite in obs["citations"]:
                        pg,num=cite["id"].split("S")
                        key=pg+"S"+str(int(num)+1)
                        sentence=spans.get(key,"")
                        if re.search(r"(?:后续|尚需|需)(?:观察|等待).{1,28}(?:再做判断|再作判断|再决定)",sentence):
                            following.append(dict(id=key,page=cite["page"],sentence=sentence,quote=sentence,subject="",
                                                  predicate=sentence,role="相邻原句中的待确认条件"))
                if following:
                    cards[tenor]["conditions"]=collect_evidence(cards[tenor]["conditions"]+following)
            rejected.extend(dict(tenor=tenor,reason="品种或判断不足以换算为卡片",item=f) for f in skipped)
    return dict(pages=len(pages),cards=cards,portfolio=portfolio,rejected=rejected)


def main():
    from googleapiclient.http import MediaIoBaseUpload
    from openai import OpenAI
    from replay_weeklies import SAMPLES, drive_service, read_trial_inputs
    from round_two import MODEL, REFERENCE_SHA256
    from trial_evidence_cache import obtain, prior_receipts
    drive=drive_service()
    sources=read_trial_inputs(drive)
    version=extractor_version(MODEL)
    seed=prior_receipts(drive,version)
    dest=drive.files().create(body={"name":"四篇周报-第三轮证据卡-"+os.environ["GITHUB_RUN_ID"]+".json",
            "mimeType":"application/json"},fields="id,shared").execute()
    if dest.get("shared"):
        raise RuntimeError("Expected private receipt")
    result=dict(model=MODEL,reference_sha256=REFERENCE_SHA256,extractor_version=version,reports=[])
    def save():
        data=json.dumps(result,ensure_ascii=False,indent=2).encode()
        drive.files().update(fileId=dest["id"],media_body=MediaIoBaseUpload(io.BytesIO(data),mimetype="application/json")).execute(num_retries=2)
    client=OpenAI(timeout=240,max_retries=1)
    with tempfile.TemporaryDirectory() as temp:
        db=connect(os.environ.get("TRIAL_LEDGER") or Path(temp)/"cards.sqlite")
        for (name,broker,day),(source,pdf) in zip(SAMPLES,sources,strict=True):
            digest=hashlib.sha256(pdf).hexdigest()
            def read():
                _,spans=pages_and_spans(pdf)
                return select(client,pdf,spans)
            mode=[]
            def get_private():
                value,origin=obtain(drive,version,digest,MODEL,selection_schema(),seed,read)
                mode.append(origin)
                return value
            (raw,request),cache=cached_extract(db,digest,version,get_private)
            report=process(pdf,raw)
            ingest(db,source["id"],digest,broker,day,report["cards"],report["portfolio"])
            result["reports"].append(dict(name=name,source=source["id"],sha256=digest,broker=broker,day=day,raw=raw,request=request,cache=mode[0] if mode else cache,**report))
            save()
            print(broker,day,"cards",sum(v is not None for v in report["cards"].values()),flush=True)
        original=snapshot(db)
        for report in reversed(result["reports"]):
            ingest(db,report["source"],report["sha256"],report["broker"],report["day"],report["cards"],report["portfolio"])
        assert original==snapshot(db) and not original["disputed"]
        def no_model():
            raise AssertionError("Cold read called extractor")
        for r in result["reports"]:
            value,origin=obtain(drive,version,r["sha256"],MODEL,selection_schema(),{},no_model)
            assert origin=="Drive命中" and value==[r["raw"],r["request"]]
        result["cold_cache_hits"]=len(result["reports"])
        result.update(snapshot=original,replay_identical=True)
        save()
        db.close()

def extractor_version(model):
    import ast
    import inspect
    source=Path(__file__).with_name("round_two.py").read_text(encoding="utf8")
    tree=ast.parse(source)
    invoke=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="invoke")
    inputs=[model,PROMPT,selection_schema(),inspect.getsource(pages_and_spans),
            inspect.getsource(select),ast.get_source_segment(source,invoke)]
    return hashlib.sha256(encode(inputs).encode()).hexdigest()


if __name__=="__main__":
    main()
