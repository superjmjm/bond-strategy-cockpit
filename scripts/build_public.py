"""Build the static three-page cockpit from real reports and the existing daily benchmark."""
import argparse
import json
from pathlib import Path
from shutil import copyfile
from anchored_snapshot import snapshot

def build(root, destination, production):
    destination.mkdir(parents=True,exist_ok=True)
    data=json.loads((root/'dashboard_data.json').read_bytes())
    def publish(folder, source, isolated):
        folder.mkdir(parents=True, exist_ok=True)
        reports=json.loads(source.read_bytes())
        document=snapshot(data,reports,isolated)
        (folder/'snapshot.json').write_text(json.dumps(document,ensure_ascii=False,allow_nan=False),encoding='utf8')
        for name in ('index.html','cockpit.js','cockpit.css'):
            copyfile(root/'web'/name,folder/name)
    from build_zip_a import publish as publish_a
    publish_a(root,destination/"preview-a")
    copyfile(root/'dashboard_data.json',destination/'dashboard_data.json')
    if (root/'preview/reports.json').exists():
        publish(destination/'preview',root/'preview/reports.json',True)
    if production:
        publish(destination,root/'anchored_reports.json',False)
    else:
        copyfile(root/'index.html',destination/'index.html')

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('destination',type=Path)
    p.add_argument('--production',action='store_true')
    a=p.parse_args()
    build(Path(__file__).resolve().parents[1],a.destination,a.production)
