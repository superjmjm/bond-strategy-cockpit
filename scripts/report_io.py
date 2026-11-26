"""Fetch eligible NXNY PDFs, reuse Drive backups, and verify uploads before extraction."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
from datetime import date, timedelta, datetime, timezone
from pathlib import Path

import requests

if __package__:
    from .report_scope import manual_identity, weekly_broker
else:
    from report_scope import manual_identity, weekly_broker

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "reports_inbox"
LIST_URL = "https://www.nxny.com/stype_qh/"
MAX_LIST_PAGES = 30
MAX_BYTES = 48_000_000
CN = timezone(timedelta(hours=8))


def env(key):
    value = os.environ.get(key, "").strip()
    if not value:
        raise ValueError(f"Missing configuration {key}")
    return value


def drive_service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    info = json.loads(env("GDRIVE_OAUTH_JSON"))
    creds = Credentials(None, refresh_token=info["refresh_token"], client_id=info["client_id"],
                        client_secret=info["client_secret"], token_uri="https://oauth2.googleapis.com/token")
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def list_pdfs(drive, folder):
    if not re.fullmatch(r"[\w-]+", folder):
        raise ValueError("Invalid Drive folder ID")
    result, token = [], None
    while True:
        page = drive.files().list(q=f"'{folder}' in parents and trashed=false and mimeType='application/pdf'",
            pageSize=1000, pageToken=token, supportsAllDrives=True, includeItemsFromAllDrives=True,
            fields="nextPageToken,files(id,name,md5Checksum,size,modifiedTime,appProperties)",
            orderBy="modifiedTime desc").execute(num_retries=2)
        result.extend(page.get("files", []))
        token = page.get("nextPageToken")
        if not token:
            return result


def valid(pdf):
    if not pdf.startswith(b"%PDF-") or len(pdf) >= MAX_BYTES:
        raise ValueError("Invalid PDF or exceeds input limit")
    return pdf


def download_cached(drive, row):
    if not 0 < int(row.get("size", 0)) < MAX_BYTES:
        raise ValueError("Drive PDF size outside input limits")
    pdf = valid(drive.files().get_media(fileId=row["id"], supportsAllDrives=True).execute(num_retries=2))
    if hashlib.md5(pdf).hexdigest() != row["md5Checksum"]:
        raise ValueError("Drive download checksum mismatch")
    return pdf


def parse_listing(body):
    items = {}
    for block in re.findall(r"<li(?:\s[^>]*)?>(.*?)</li>", body, re.S | re.I):
        match = re.search(r'<a\s[^>]*href="(/report/view_(\d+)\.html)"[^>]*title="([^"]+)"', block)
        if not match or 'class="rlist"' not in block:
            continue
        title = html.unescape(match[3])
        try:
            day = datetime.strptime(title.rsplit("-", 1)[-1], "%y%m%d").date()
        except ValueError:
            day = None
        uploaded = re.search(r"上传时间\s*[：:]\s*(\d{8})\b", block)
        try:
            upload_day = datetime.strptime(uploaded[1], "%Y%m%d").date() if uploaded else None
        except ValueError:
            upload_day = None
        items[match[2]] = (title, day, upload_day)
    if not items:
        raise ValueError("NXNY list unavailable or structure changed")
    return items


def scan_listing(today, seen_ids, archived_names, limit, target="", days=7):
    """Read public list pages in upload order; never download a PDF here."""
    cutoff = today - timedelta(days=days)
    reports = []
    fingerprints = set()
    seen_ids = set(seen_ids)
    max_pages = MAX_LIST_PAGES * ((days + 6) // 7)
    for page in range(1, max_pages + 1):
        url = LIST_URL if page == 1 else f"{LIST_URL.rstrip('/')}_p{page}/"
        try:
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=45)
            response.raise_for_status()
            body = response.content.decode("utf8")
            items = parse_listing(body)
        except (requests.RequestException, ValueError) as error:
            return reports, page, f"page {page}: {type(error).__name__}"
        fingerprint = frozenset(items)
        if fingerprint in fingerprints:
            return reports, page, f"page {page}: repeated listing"
        fingerprints.add(fingerprint)
        for rid, (title, report_day, _) in items.items():
            if rid in seen_ids or (target and rid != target):
                continue
            if not report_day or not cutoff <= report_day <= today or not weekly_broker(title):
                continue
            if f"{rid}-{name_for(title)}.pdf" in archived_names:
                continue
            reports.append((rid, title))
            seen_ids.add(rid)
            if len(reports) == limit:
                return reports, page, None

        uploads = [upload for _, _, upload in items.values()]
        if all(upload and upload < cutoff for upload in uploads):
            return reports, page, None
        next_pages = {int(n) for n in re.findall(
            r"""href\s*=\s*["'](?:https://www\.nxny\.com)?/stype_qh_p(\d+)/["']""", body
        ) if int(n) > page}
        if not next_pages:
            return reports, page, None
        if page + 1 not in next_pages:
            return reports, page, f"page {page}: next page missing"
    return reports, max_pages, "list page limit reached before completing discovery"


def fetch_nxny(rid):
    # Use the existing authenticated cloud browser profile; PDF URLs stay in that browser.
    from playwright.sync_api import sync_playwright
    import base64
    profile = env("TINYFISH_PROFILE_ID")
    if not re.fullmatch(r"[\w-]+", profile):
        raise ValueError("Invalid browser profile")
    api = f"https://agent.tinyfish.ai/v1/profiles/{profile}/setup-session"
    auth = {"X-API-Key": env("TINYFISH_API_KEY")}
    detail = f"https://www.nxny.com/report/view_{rid}.html"
    response = requests.post(api, headers=auth, json={"url":detail,"timeout_seconds":180}, timeout=70)
    response.raise_for_status()
    session = response.json()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(session["cdp_url"], timeout=60000)
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(detail, wait_until="domcontentloaded", timeout=45000)
            page.locator("#loginstr2").filter(has_text="退出登录").wait_for(timeout=15000)
            hrefs = page.locator("a[href]").evaluate_all("els => els.map(a=>a.href)")
            links = {u for u in hrefs if re.match(r"^https://vip6\.nxny\.com/download/download\.aspx(?:\?|$)",u,re.I)}
            if len(links) != 1:
                raise ValueError("No unique NXNY PDF download control")
            cdp = context.new_cdp_session(page)
            frame = cdp.send("Page.getFrameTree")["frameTree"]["frame"]["id"]
            result = cdp.send("Network.loadNetworkResource", {"frameId":frame, "url":links.pop(),
                    "options":{"disableCache":True,"includeCredentials":True}})["resource"]
            if not result.get("success") or not result.get("stream"):
                raise IOError("NXNY download failed")
            handle = result["stream"]
            pdf = bytearray()
            try:
                while True:
                    part = cdp.send("IO.read", {"handle":handle,"size":131072})
                    pdf.extend(base64.b64decode(part["data"]) if part.get("base64Encoded") else part["data"].encode())
                    if len(pdf) >= MAX_BYTES:
                        raise ValueError("PDF exceeds input limit")
                    if part.get("eof"):
                        break
            finally:
                cdp.send("IO.close", {"handle":handle})
            return valid(bytes(pdf))
    finally:
        requests.post(api+"/cancel", headers=auth, json={"session_id":session["session_id"]},
                      timeout=45).raise_for_status()


def name_for(title):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", title).strip(" .")
    if len(name) > 80 and re.search(r"-\d{6}$", name):
        return name[:73] + name[-7:]
    return name[:80]


def recover_history(drive, cutoff, today, skip_ids, published, limit):
    from googleapiclient.errors import HttpError
    files = drive.files()
    index = files.list(q="trashed=false and name='研报完整目录.json' and mimeType='application/json'",
                       fields="files(id),nextPageToken", pageSize=2).execute(num_retries=2)
    rows = index.get("files", [])
    if len(rows) != 1 or index.get("nextPageToken"):
        raise ValueError("Historical Drive catalog missing or ambiguous")
    catalog = json.loads(files.get_media(fileId=rows[0]["id"]).execute(num_retries=2))
    count = 0
    for report in catalog["reports"]:
        title, rid = re.sub(r"\.pdf$", "", report.get("title", ""), flags=re.I), str(report.get("report_id") or "")
        digest = report.get("sha256")
        if (rid in skip_ids or digest in published or not weekly_broker(title)
                or not re.fullmatch(r"[0-9a-f]{64}", digest or "") or not report.get("pdf_id")):
            continue
        stamp = re.search(r"-(\d{6})$", title)
        if not stamp or not cutoff <= datetime.strptime(stamp[1], "%y%m%d").date() <= today:
            continue
        try:
            row = files.get(fileId=report["pdf_id"], fields="id,size,md5Checksum,trashed",
                            supportsAllDrives=True).execute(num_retries=2)
        except HttpError as exc:
            if exc.resp.status == 404:
                continue
            raise
        if row.get("trashed"):
            continue
        raw = download_cached(drive, row)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Historical catalog digest mismatch")
        prefix = rid + "-" if rid.isdigit() else ""
        (INBOX / f"{prefix}{name_for(title)}.pdf").write_bytes(raw)
        if rid.isdigit():
            skip_ids.add(rid)
        published.add(digest)
        count += 1
        if count >= limit:
            break
    return count


def fetch(drive, folder, *, archive_only=False):
    target = os.environ.get("NXNY_REPORT_ID", "")
    if (target or archive_only) and not re.fullmatch(r"[0-9]{1,12}", target):
        raise ValueError("NXNY report ID must be numeric")
    INBOX.mkdir(exist_ok=True)
    records = json.loads((ROOT/"dashboard_data.json").read_text("utf8"))["records"]
    anchored = ROOT/"anchored_reports.json"
    anchored_rows = json.loads(anchored.read_bytes()) if anchored.exists() else []
    anchored_hashes = {r["report_id"] for r in anchored_rows}
    days = int(os.environ.get("REPORT_LOOKBACK_DAYS") or "7")
    if not 1 <= days <= 56:
        raise ValueError("REPORT_LOOKBACK_DAYS must be in 1..56")
    today = datetime.now(CN).date()
    cutoff = today - timedelta(days=days)
    records = anchored_rows if days > 7 else records + anchored_rows
    known = {row.get("report_id") for row in records}
    published_ids = {
        match[1] for row in records if not row.get("is_demo")
        if (match := re.match(r"^(\d+)-", row.get("source_file") or ""))
    }
    backups = list_pdfs(drive, folder)
    prior = {r["name"]:r for r in backups}
    by_id = {r["appProperties"]["nxny_id"]:r for r in backups if r.get("appProperties",{}).get("nxny_id")}
    if archive_only and target not in by_id:
        raise ValueError("Selected report is absent from the Drive archive")
    limit = 1 if target else int(os.environ.get("MAX_NEW_PDFS") or "15")
    if not 1 <= limit <= 20:
        raise ValueError("MAX_NEW_PDFS must be in 1..20")
    # Resume any backed-up but unpublished reports, even after they have left the listing.
    recovered = 0
    failures = []
    for row in backups:
        if recovered >= limit and not target:
            break
        metadata = row.get("appProperties", {})
        if target and metadata.get("nxny_id") != target:
            continue
        digest = metadata.get("sha256")
        try:
            manual = manual_identity(row["name"]) if not target else None
            if manual:
                if days > 7 and not cutoff <= date.fromisoformat(manual["report_date"]) <= today:
                    continue
                if digest in anchored_hashes:
                    continue
                raw = download_cached(drive, row)
                actual = hashlib.sha256(raw).hexdigest()
                if digest and actual != digest:
                    raise ValueError("Manual PDF checksum mismatch")
                drive.files().update(fileId=row["id"], supportsAllDrives=True,
                    body={"appProperties": {**metadata, "sha256": actual, "source": "manual"}}).execute(num_retries=2)
                if actual not in anchored_hashes:
                    path = INBOX / row["name"]
                    if path.exists() and path.read_bytes() != raw:
                        raise ValueError("Conflicting manual filenames")
                    path.write_bytes(raw)
                    recovered += 1
                continue
        except Exception as error:
            failures.append(f"manual {row['id']}: {type(error).__name__}")
            continue
        if days > 7:
            match = re.search(r"-(\d{6})\.pdf$", row["name"], re.I)
            if not match or not 0 <= (today - datetime.strptime(match[1], "%y%m%d").date()).days <= days:
                continue
        if digest and (digest not in known or (archive_only and target)) and weekly_broker(row["name"]):
            try:
                raw = download_cached(drive,row)
                if hashlib.sha256(raw).hexdigest() != digest:
                    raise ValueError("Drive SHA-256 mismatch")
                filename = row["name"]
                if Path(filename).name != filename or "/" in filename or chr(92) in filename or not filename.endswith(".pdf"):
                    filename = digest + ".pdf"
                (INBOX/filename).write_bytes(raw)
                recovered += 1
            except Exception as error:
                failures.append(type(error).__name__)
    skip_ids = published_ids | set(by_id)
    if days > 7 and not target and recovered < limit:
        recovered += recover_history(drive, cutoff, today, skip_ids, set(known), limit - recovered)
    reports, pages = [], 0
    drain_archive = days > 7 and recovered > 0 and not target
    if recovered < limit and not archive_only and not drain_archive and (not target or target not in by_id):
        reports, pages, issue = scan_listing(datetime.now(CN).date(), skip_ids, prior, limit - recovered, target, days)
        if issue:
            failures.append("discovery: " + issue)
        if target and not reports and target not in published_ids:
            failures.append("Selected report is absent from the eligible listing")
    downloaded = 0
    for rid,title in reports:
        filename = f"{rid}-{name_for(title)}.pdf"
        if rid in by_id or filename in prior:
            continue
        if downloaded >= limit:
            break
        downloaded += 1
        try:
            (INBOX/filename).write_bytes(fetch_nxny(rid))
        except Exception as error:
            failures.append(f"{rid}: {type(error).__name__}")
            # An expired browser session should consume at most one attempted download.
            break
    print(f"Latest list candidates={len(reports)}, new attempts={downloaded}, restored={recovered}, pages={pages}")
    if failures:
        print("::warning::Fetch incomplete: "+", ".join(failures))
    if failures:
        return 1 if target else 2
    return 0


def backup(drive, folder):
    from googleapiclient.http import MediaFileUpload
    existing = {r.get("appProperties",{}).get("sha256"):r for r in list_pdfs(drive,folder)}
    for path in sorted(INBOX.glob("*.pdf")):
        raw = valid(path.read_bytes())
        sha = hashlib.sha256(raw).hexdigest()
        md5 = hashlib.md5(raw).hexdigest()
        if sha in existing:
            if existing[sha].get("md5Checksum") != md5:
                raise ValueError("Existing Drive backup checksum mismatch")
            continue
        props = {"sha256":sha}
        rid = re.match(r"^(\d+)-",path.name)
        if rid:
            props["nxny_id"] = rid[1]
        file = drive.files().create(body={"name":path.name,"parents":[folder],"appProperties":props},
                media_body=MediaFileUpload(str(path),mimetype="application/pdf",resumable=True),
                fields="id,md5Checksum,size",supportsAllDrives=True).execute()
        if file.get("md5Checksum") != md5 or int(file["size"]) != len(raw):
            raise ValueError("Drive upload verification failed")
        existing[sha] = file
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["fetch","backup"])
    parser.add_argument("--archive-only", action="store_true")
    args = parser.parse_args()
    if args.archive_only and args.action != "fetch":
        parser.error("--archive-only is only available for fetch")
    try:
        drive, folder = drive_service(), env("GDRIVE_FOLDER_ID")
        if args.action == "fetch":
            sys.exit(fetch(drive, folder, archive_only=args.archive_only))
        sys.exit(backup(drive, folder))
    except Exception as error:
        # Remote exceptions can contain signed URLs; log class and stage only.
        print(f"::error::{args.action} failed ({type(error).__name__}). Check configuration and provider status.")
        sys.exit(1)
