from datetime import date

BUCKETS = ['整体久期/组合','短端(1-3Y)','中端(5-7Y)','10Y国债','30Y超长债','曲线形态']

def snapshot(data,reports,isolated):
    ids=set()
    for r in reports:
        if r['report_id'] in ids:
            raise ValueError('Duplicate report')
        ids.add(r['report_id'])
        date.fromisoformat(r['report_date'])
        buckets=set()
        for c in r['cards']:
            if c['bucket'] not in BUCKETS or c['bucket'] in buckets or type(c['vote_eligible']) is not bool:
                raise ValueError('Invalid card')
            if type(c['direction']) is not int or not -2<=c['direction']<=2 or not c['evidence']:
                raise ValueError('Invalid direction or unanchored card')
            buckets.add(c['bucket'])
    legacy=[] if isolated else [r for r in data['records'] if not r.get('is_demo') and r['report_id'] not in ids]
    return dict(version='1c138058+cover',isolated=isolated,buckets=BUCKETS,
                benchmark={k:data.get(k) for k in ('benchmark_as_of','benchmark_yields','benchmark_source_url','benchmark_note')},
                reports=sorted(reports,key=lambda r:(r['report_date'],r['report_id']),reverse=True),legacy=legacy)
