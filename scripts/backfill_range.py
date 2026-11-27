"""Inventory and restore one explicit report-date interval."""
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import report_io as io
from report_scope import BROKERS, covered_broker, weekly_broker

STATE = Path(os.environ.get("RUNNER_TEMP", ".")) / "range-inventory.json"


def parameters():
    dates = []
    for key in ("START_DATE", "END_DATE"):
        value = os.environ[key]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError(f"Invalid {key}: expected YYYY-MM-DD")
        dates.append(date.fromisoformat(value))
    if dates[0] > dates[1]:
        raise ValueError("start_date must precede or equal end_date")
    raw = os.environ.get("BROKERS", "").strip()
    brokers = list(dict.fromkeys(covered_broker(s) for s in raw.split(","))) if raw else list(BROKERS)
    if any(b not in BROKERS for b in brokers):
        raise ValueError("Unknown or non-automatic broker")
    limit = int(os.environ["MAX_DOWNLOADS"])
    if not 1 <= limit <= 15:
        raise ValueError("max_downloads must be in 1..15")
    return *dates, brokers, limit


def published():
    return json.loads((io.ROOT / "anchored_reports.json").read_bytes())


def source_id(filename):
    match = re.match(r"^(\d+)-", filename)
    return match[1] if match else None


def archive_rows(drive):
    for row in io.list_pdfs(drive, io.env("GDRIVE_FOLDER_ID")):
        props = row.get("appProperties", {})
        yield row["name"], props.get("nxny_id") or source_id(row["name"]), row.get("sha256Checksum") or props.get("sha256"), row["id"]
    for row in io.historical_catalog(drive)["reports"]:
        title = re.sub(r"\.pdf$", "", row.get("title", ""), flags=re.I)
        rid = str(row.get("report_id") or "")
        prefix = rid + "-" if rid.isdigit() else ""
        if row.get("pdf_id"):
            yield prefix + io.name_for(title) + ".pdf", rid or None, row.get("sha256"), row["pdf_id"]


def scan_channel(channel, start, end, brokers, ignored, rows, hits, skipped):
    retry = Retry(total=3, backoff_factor=1, allowed_methods={"GET"}, status_forcelist=(429, 502, 503, 504))
    with requests.Session() as session:
        session.mount("https://", HTTPAdapter(max_retries=retry))
        return scan_pages(session, channel, start, end, brokers, ignored, rows, hits, skipped)


def scan_pages(session, channel, start, end, brokers, ignored, rows, hits, skipped):
    page = 1
    fingerprints = set()
    while True:
        suffix = "" if page == 1 else f"_p{page}"
        url = f"{io.LIST_BASE}/stype_{channel}{suffix}/"
        response = session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=45)
        response.raise_for_status()
        body = response.content.decode("utf8")
        uploads = []
        identifiers = []

        def observe(rid, title, day, upload, extension):
            uploads.append(upload)
            identifiers.append(rid)
            extension = extension or ignored.get(rid)
            if not extension:
                return
            clean = re.sub(r"\." + re.escape(extension) + r"(?![a-z0-9])", "", title, flags=re.I)
            # A filename suffix can follow the six-digit date.
            stamp = re.search(r"-(\d{6})$", clean)
            if not day and stamp:
                day = datetime.strptime(stamp[1], "%y%m%d").date()
            broker = weekly_broker(clean, channel=channel)
            if broker and broker[0] in brokers and day and start <= day <= end:
                skipped[broker[0]].add(rid)
                ignored[rid] = extension
                hits[broker[0]].add(rid)

        items = io.parse_listing(body, observe=observe)
        fingerprint = frozenset(identifiers)
        if fingerprint in fingerprints:
            raise ValueError(f"Repeated {channel} listing page {page}")
        fingerprints.add(fingerprint)
        for rid, (title, day, _, pages) in items.items():
            broker = weekly_broker(title, pages=pages, channel=channel)
            if day and start <= day <= end and broker and broker[0] in brokers and rid not in ignored:
                hits[broker[0]].add(rid)
                rows.setdefault(rid, (broker[0], title, pages, channel))
        if uploads and all(d and d < start for d in uploads):
            return page
        links = {int(n) for n in re.findall(
            rf"""href\s*=\s*["'](?:https://www\.nxny\.com)?/stype_{channel}_p(\d+)/["']""", body)}
        if not any(n > page for n in links):
            return page
        if page + 1 not in links:
            raise ValueError(f"Missing {channel} listing page after {page}")
        page += 1


def run():
    from googleapiclient.errors import HttpError

    start, end, brokers, limit = parameters()
    dry = os.environ["DRY_RUN"].lower() == "true"
    initial = published()
    digests = {r["report_id"] for r in initial}
    ids = {source_id(r["source_file"]) for r in initial} - {None}
    names = {r["source_file"] for r in initial}
    before = defaultdict(list)
    for r in initial:
        if r["broker"] in brokers and start.isoformat() <= r["report_date"] <= end.isoformat():
            before[r["broker"]].append(r["report_id"])
    archives = {}
    archive_counts = defaultdict(list)
    inaccessible = defaultdict(list)
    drive = io.drive_service()
    for name, rid, digest, file_id in archive_rows(drive):
        stamp = re.search(r"-(\d{6})\.pdf$", name, re.I)
        broker = weekly_broker(name)
        if not stamp or not broker or broker[0] not in brokers:
            continue
        day = datetime.strptime(stamp[1], "%y%m%d").date()
        if not start <= day <= end or rid in ids or digest in digests or name in names:
            continue
        if "/" in name or "\\" in name:
            raise ValueError("Unsafe archive filename")
        try:
            meta = drive.files().get(fileId=file_id, fields="id,size,md5Checksum,trashed", supportsAllDrives=True).execute(num_retries=2)
        except HttpError as exc:
            if exc.resp.status != 404:
                raise
            meta = None
        if not meta or meta.get("trashed") or not meta.get("md5Checksum") or not 0 < int(meta.get("size", 0)) < io.MAX_BYTES:
            inaccessible[broker[0]].append(name)
            continue
        archives[file_id] = (name, digest, meta)
        archive_counts[broker[0]].append(name)
        names.add(name)
        if rid:
            ids.add(rid)
        if digest:
            digests.add(digest)
    rows, hits, skipped = {}, defaultdict(set), defaultdict(set)
    ignored = json.loads((io.ROOT / "ignored_nxny_ids.json").read_bytes())
    pages = {ch: scan_channel(ch, start, end, brokers, ignored, rows, hits, skipped) for ch in ("qh", "hc")}
    candidates = {rid: row for rid, row in rows.items() if rid not in ids and f"{rid}-{io.name_for(row[1])}.pdf" not in names}
    state = dict(before=before, archive=archive_counts, inaccessible=inaccessible, candidates={b: [rid for rid, row in candidates.items() if row[0] == b] for b in brokers},
                 hits={b: sorted(hits[b]) for b in brokers}, skipped={b: sorted(skipped[b]) for b in brokers}, pages=pages)
    def checkpoint():
        STATE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf8")
    checkpoint()
    if dry:
        return
    io.INBOX.mkdir(exist_ok=True)
    for name, digest, meta in archives.values():
        raw = io.download_cached(drive, meta)
        if digest and hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Archive digest mismatch")
        io.save_recovered(name, raw)
    manifest = {}
    partial = False
    for rid, (broker, title, pages, channel) in list(candidates.items())[:limit]:
        name = f"{rid}-{io.name_for(title)}.pdf"
        try:
            raw = io.valid(io.fetch_nxny(rid))
        except io.NonPDFReport as exc:
            ignored[rid] = str(exc)
            state["skipped"][broker].append(rid)
            checkpoint()
            continue
        except Exception as exc:
            reason = str(exc) if isinstance(exc, io.NXNYDownloadError) else type(exc).__name__
            print(f"::warning::Download incomplete for {rid}: {reason}")
            partial = True
            break
        io.save_recovered(name, raw)
        io.backup(drive, io.env("GDRIVE_FOLDER_ID"), [io.INBOX / name])
        manifest[name] = dict(title=title, pages=pages, channel=channel)
    io.save_ignored_ids(ignored)
    (io.INBOX / "listing.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf8")
    return 2 if partial else 0


def summary():
    start, end, brokers, _ = parameters()
    dry = os.environ["DRY_RUN"].lower() == "true"
    lines = [f"### {start} 至 {end} · " + ("只读盘点" if dry else "补录结果")]
    if not STATE.exists():
        lines.append("盘点未完成，无法判定券商缺口。请查看失败步骤。")
    else:
        s = json.loads(STATE.read_bytes())
        committed = os.environ.get("COMMITTED") == "true"
        records = published() if committed else []
        lines += [f"NXNY 列表页数：{s['pages']}。待处理两列为执行前盘点。", "",
                  "|券商|此前已入库|本次新增入库|Drive 待恢复|NXNY 待下载|非PDF已跳过|源站列表未见|",
                  "|---|---:|---:|---:|---:|---:|---:|---|"]
        for b in brokers:
            old = s["before"].get(b, [])
            new = sum(r["broker"] == b and start.isoformat() <= r["report_date"] <= end.isoformat() and r["report_id"] not in old for r in records)
            lines.append(f"|{b}|{len(old)}|{new}|{len(s['archive'].get(b, []))}|{len(s['candidates'][b])}|{len(s['skipped'][b])}|{'是' if not s['hits'][b] else '—'}|")
        for b, names in s.get("inaccessible", {}).items():
            lines.append(f"\n{b}有 {len(names)} 个归档目录条目无法读取，未计入 Drive 待恢复。")
    result = "\n".join(lines)
    print(result)
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf8") as out:
        print(result, file=out)


if __name__ == "__main__":
    sys.exit(summary() if sys.argv[1:] == ["summary"] else run())
