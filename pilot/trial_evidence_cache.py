"""Private four-PDF cache. The trial workflow serializes jobs sharing this Drive namespace."""
import hashlib
import io
import json

from googleapiclient.http import MediaIoBaseUpload
from jsonschema import validate

from evidence_cards import encode

SEED_ID = "1xpXALUBBSZq7lq_fg0G0duXU4nEeFH7H"
SEED_HASH = "76a432164381c5f8f5371d3f8b7f7fff77bf90dee6c836137e88b161b0aaa811"
SEED_VERSION = "f6653b92ad3a50f78ab27e77fc0e5fe6e45a73c5392d9d1023727ddac6b6dc77"


def lookup(drive,key):
    rows=drive.files().list(q="trashed=false and appProperties has { key='weeklies3' and value='"+key+"' }",
        fields="nextPageToken,files(id,shared)",pageSize=2).execute(num_retries=2)
    found=rows.get("files",[])
    if len(found)>1 or rows.get("nextPageToken") or any(f.get("shared",True) for f in found):
        raise ValueError("Ambiguous or shared trial evidence cache")
    return found[0]["id"] if found else None


def cached_value(drive,fid,key,model,schema):
    body=json.loads(drive.files().get_media(fileId=fid).execute(num_retries=2))
    raw,req=body["value"]
    if body["key"]!=key or req["model"]!=model or not req["id"] or hashlib.sha256(encode(body["value"]).encode()).hexdigest()!=body["sha256"]:
        raise ValueError("Evidence cache receipt mismatch")
    validate(raw,schema)
    return [raw,req]


def prior_receipts(drive,version):
    if version!=SEED_VERSION:
        return {}
    data=drive.files().get_media(fileId=SEED_ID).execute(num_retries=2)
    if hashlib.sha256(data).hexdigest()!=SEED_HASH:
        raise ValueError("Archived model receipt changed")
    result=json.loads(data)
    if not result["replay_identical"] or len(result["reports"])!=4:
        raise ValueError("Incomplete archived trial")
    return {r["sha256"]:[r["raw"],r["request"]] for r in result["reports"]}


def obtain(drive,version,digest,model,schema,seed,read):
    key=hashlib.sha256((version+":"+digest).encode()).hexdigest()
    fid=lookup(drive,key)
    if fid:
        return cached_value(drive,fid,key,model,schema),"Drive命中"
    if digest in seed:
        value=seed[digest]
        mode="已核验的先前运行回执"
    else:
        value=read()
        mode="新模型请求"
    validate(value[0],schema)
    if value[1]["model"]!=model:
        raise ValueError("Unexpected model")
    data=encode(dict(key=key,value=value,sha256=hashlib.sha256(encode(value).encode()).hexdigest())).encode()
    row=drive.files().create(body={"name":"四篇试验-原文证据缓存-"+key[:16]+".json","appProperties":{"weeklies3":key}},
        media_body=MediaIoBaseUpload(io.BytesIO(data),mimetype="application/json"),fields="id,shared").execute()
    if row.get("shared",True):
        raise ValueError("Evidence cache must be private")
    return cached_value(drive,row["id"],key,model,schema),mode
