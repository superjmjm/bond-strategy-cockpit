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
    from .report_scope import weekly_broker
else:
    from report_scope import weekly_broker

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "reports_inbox"
LIST_URL = "https://www.nxny.com/stype_qh/"
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


def discover(body, today):
    items = {}
    for block in re.findall(r"<li(?:\s[^>]*)?>(.*?)</li>", body, re.S | re.I):
        match = re.search(r'<a\s[^>]*href="(/report/view_(\d+)\.html)"[^>]*title="([^"]+)"', block)
        if not match or 'class="rlist"' not in block:
            continue
        title = html.unescape(match[3])
        try:
            day = datetime.strptime(title.rsplit("-", 1)[-1], "%y%m%d").date()
        except ValueError:
            continue
        items[match[2]] = (title, day)
    if not items:
        raise ValueError("NXNY list unavailable or structure changed")
    return [(rid,title) for rid,(title,day) in items.items()
            if today-timedelta(days=7) <= day <= today and weekly_broker(title)]


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
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", title).strip(" .")[:80]


def fetch(drive, folder):
    target = os.environ.get("NXNY_REPORT_ID", "")
    if target and not re.fullmatch(r"[0-9]{1,12}", target):
        raise ValueError("NXNY report ID must be numeric")
    INBOX.mkdir(exist_ok=True)
    known = {r.get("report_id") for r in json.loads((ROOT/"dashboard_data.json").read_text("utf8"))["records"]}
    backups = list_pdfs(drive, folder)
    prior = {r["name"]:r for r in backups}
    by_id = {r["appProperties"]["nxny_id"]:r for r in backups if r.get("appProperties",{}).get("nxny_id")}
    limit = 1 if target else int(os.environ.get("MAX_NEW_PDFS") or "4")
    if not 1 <= limit <= 20:
        raise ValueError("MAX_NEW_PDFS must be in 1..20")
    # Resume any backed-up but unpublished reports, even after they have left the listing.
    recovered = 0
    failures = []
    for row in backups:
        metadata = row.get("appProperties", {})
        if target and metadata.get("nxny_id") != target:
            continue
        digest = metadata.get("sha256")
        if digest and digest not in known and weekly_broker(row["name"]):
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
    reports = []
    if not target or target not in by_id:
        try:
            response = requests.get(LIST_URL, headers={"User-Agent":"Mozilla/5.0"},timeout=45)
            response.raise_for_status()
            reports = discover(response.content.decode("utf8"), datetime.now(CN).date())
        except Exception as error:
            failures.append("discovery: "+type(error).__name__)
        if target:
            reports = [(rid, title) for rid, title in reports if rid == target]
            if not reports:
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
    print(f"Latest list candidates={len(reports)}, new attempts={downloaded}, restored={recovered}")
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
    args = parser.parse_args()
    try:
        sys.exit((fetch if args.action=="fetch" else backup)(drive_service(),env("GDRIVE_FOLDER_ID")))
    except Exception as error:
        # Remote exceptions can contain signed URLs; log class and stage only.
        print(f"::error::{args.action} failed ({type(error).__name__}). Check configuration and provider status.")
        sys.exit(1)
