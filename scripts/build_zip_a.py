"""Package the original ECharts dashboard with as-of weekly snapshots."""
import json
import sys
from pathlib import Path
from shutil import copyfile
from datetime import date,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'zip_a'))
from build_snapshot import build_snapshot,infer_week_mon

def publish(root,folder):
    reports=json.loads((root/'anchored_reports.json').read_bytes())
    data=json.loads((root/'dashboard_data.json').read_bytes())
    history=dict(data.get('benchmark_history',{}))
    history[data['benchmark_as_of']]=data['benchmark_yields']
    yields=[{'date':day,'2Y':v['1-3Y'],'7Y':v['5-7Y'],'10Y':v['10Y'],'30Y':v['30Y']} for day,v in sorted(history.items())]
    weeks=sorted({infer_week_mon(r['report_date']) for r in reports})
    docs=[]
    for mon in weeks:
        end=(date.fromisoformat(mon)+timedelta(days=6)).isoformat()
        rs=[r for r in reports if infer_week_mon(r['report_date'])<=mon]
        views=[dict(c,broker=r['broker'],report_id=r['report_id'],series=r['series'],report_date=r['report_date'],tenor=c['bucket'],quote=c['evidence'][0]['text'],page=c['evidence'][0]['page']) for r in rs for c in r['cards']]
        doc=build_snapshot(views,[v for v in yields if v['date']<=end],rs)
        doc['empty_reports']=[dict(r,week_mon=infer_week_mon(r['report_date'])) for r in rs if not r['cards']]
        docs.append(doc)
    (folder/'data').mkdir(parents=True,exist_ok=True)
    (folder/'data/snapshot.json').write_text(json.dumps({'as_of':docs},ensure_ascii=False,allow_nan=False),encoding='utf8')
    for name in ('index.html','app.js','styles.css','echarts.min.js'):
        copyfile(root/'zip_a'/name,folder/name)
