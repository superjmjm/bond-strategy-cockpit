"""Stage verified historical Drive originals through a private application-owned Drive file."""

import hashlib
import io
import json
import os
from pathlib import PurePosixPath
import time
from zipfile import ZipFile

from report_io import INBOX, ROOT, drive_service, valid
from report_scope import weekly_broker

BUNDLE_SHA256 = "77790243547d9d51841fab5107280c9ca318fb68a0d6351f07e8cae53bcd85cb"
REPORT_FILES = ('华创证券-债券周报：政策利率换锚的猜想与实际线索-261018.pdf', '华泰证券-利率周报：情绪的钟摆-261018.pdf')


def stage_bundle(raw, published):
    if hashlib.sha256(raw).hexdigest() != BUNDLE_SHA256:
        raise ValueError("Unexpected historical bundle SHA-256")
    with ZipFile(io.BytesIO(raw)) as archive:
        names = archive.namelist()
        if len(names) != len(REPORT_FILES) or len(set(names)) != len(names):
            raise ValueError("Unexpected file count in historical bundle")
        inputs = {}
        for name in names:
            if PurePosixPath(name).name != name or name not in REPORT_FILES or not weekly_broker(name):
                raise ValueError("Unexpected historical source filename")
            pdf = valid(archive.read(name))
            digest = hashlib.sha256(pdf).hexdigest()
            if digest in inputs:
                raise ValueError("Duplicate PDF SHA-256 in historical bundle")
            inputs[digest] = (name, pdf)
    INBOX.mkdir(exist_ok=True)
    for digest, (name, pdf) in inputs.items():
        if digest not in published:
            (INBOX / name).write_bytes(pdf)
    print(f"historical Drive handoff: verified={len(inputs)}, pending={len(inputs.keys() - published)}, NXNY requests=0", flush=True)


def main():
    drive = drive_service()
    files = drive.files()
    name = "cockpit-history-private-intake-" + os.environ["GITHUB_RUN_ID"] + ".zip"
    fields = "id,name,ownedByMe,shared,trashed,size,md5Checksum"
    published = {r["report_id"] for r in json.loads((ROOT / "anchored_reports.json").read_bytes())}
    row = files.create(body={"name": name, "mimeType": "application/zip"}, fields=fields).execute()
    file_id = row["id"]
    try:
        print("Awaiting private Drive input: " + name, flush=True)
        for _ in range(120):
            if not row.get("ownedByMe") or row.get("shared") or row.get("trashed"):
                raise ValueError("historical staging file must be private and owned by the production account")
            size = int(row.get("size", 0))
            if size:
                if size != 4_426_047:
                    raise ValueError("Unexpected historical bundle size")
                raw = files.get_media(fileId=file_id).execute(num_retries=2)
                stage_bundle(raw, published)
                return
            time.sleep(5)
            row = files.get(fileId=file_id, fields=fields).execute(num_retries=2)
        raise TimeoutError("historical staging bundle was not received")
    finally:
        files.update(fileId=file_id, body={"trashed": True}).execute(num_retries=2)


if __name__ == "__main__":
    main()
