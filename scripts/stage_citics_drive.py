"""Stage nine verified CITICS originals through a private application-owned Drive file."""

import hashlib
import io
import json
import os
from pathlib import PurePosixPath
import time
from zipfile import ZipFile

from report_io import INBOX, ROOT, drive_service, valid
from report_scope import manual_identity

BUNDLE_SHA256 = "9fa35969e976fd473a19b7085674a4fab46dc7c07d81ab9b7f294200d2834f81"
REPORT_DATES = {
    "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28", "2026-10-18",
    "2026-10-26", "2026-11-09", "2026-11-16", "2026-11-23",
}


def stage_bundle(raw, published):
    if hashlib.sha256(raw).hexdigest() != BUNDLE_SHA256:
        raise ValueError("Unexpected CITICS bundle SHA-256")
    with ZipFile(io.BytesIO(raw)) as archive:
        names = archive.namelist()
        if len(names) != len(REPORT_DATES) or len(set(names)) != len(names):
            raise ValueError("Unexpected file count in CITICS bundle")
        dates = set()
        inputs = {}
        for name in names:
            identity = manual_identity(name)
            if (PurePosixPath(name).name != name or not identity
                    or identity["series"] != "每周市场策略聚焦"):
                raise ValueError("Unexpected CITICS source filename")
            dates.add(identity["report_date"])
            pdf = valid(archive.read(name))
            digest = hashlib.sha256(pdf).hexdigest()
            if digest in inputs:
                raise ValueError("Duplicate PDF SHA-256 in CITICS bundle")
            inputs[digest] = (name, pdf)
        if dates != REPORT_DATES:
            raise ValueError("Unexpected report dates in CITICS bundle")
    INBOX.mkdir(exist_ok=True)
    for digest, (name, pdf) in inputs.items():
        if digest not in published:
            (INBOX / name).write_bytes(pdf)
    print(f"CITICS Drive handoff: verified={len(inputs)}, pending={len(inputs.keys() - published)}, NXNY requests=0", flush=True)


def main():
    drive = drive_service()
    files = drive.files()
    name = "cockpit-citics-private-intake-" + os.environ["GITHUB_RUN_ID"] + ".zip"
    fields = "id,name,ownedByMe,shared,trashed,size,md5Checksum"
    published = {r["report_id"] for r in json.loads((ROOT / "anchored_reports.json").read_bytes())}
    row = files.create(body={"name": name, "mimeType": "application/zip"}, fields=fields).execute()
    file_id = row["id"]
    try:
        print("Awaiting private Drive input: " + name, flush=True)
        for _ in range(120):
            if not row.get("ownedByMe") or row.get("shared") or row.get("trashed"):
                raise ValueError("CITICS staging file must be private and owned by the production account")
            size = int(row.get("size", 0))
            if size:
                if size != 11_354_904:
                    raise ValueError("Unexpected CITICS bundle size")
                raw = files.get_media(fileId=file_id).execute(num_retries=2)
                stage_bundle(raw, published)
                return
            time.sleep(5)
            row = files.get(fileId=file_id, fields=fields).execute(num_retries=2)
        raise TimeoutError("CITICS staging bundle was not received")
    finally:
        files.update(fileId=file_id, body={"trashed": True}).execute(num_retries=2)


if __name__ == "__main__":
    main()
