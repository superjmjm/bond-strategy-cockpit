"""Fetch eligible NXNY PDFs, reuse Drive backups, and verify uploads before extraction."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import tempfile
from datetime import date, timedelta, datetime, timezone
from pathlib import Path

import requests

if __package__:
    from .report_scope import manual_identity, non_pdf_suffix, weekly_broker
else:
    from report_scope import manual_identity, non_pdf_suffix, weekly_broker

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "reports_inbox"
LIST_BASE = "https://www.nxny.com"
MAX_LIST_PAGES = 30
MAX_BYTES = 48_000_000
CN = timezone(timedelta(hours=8))


class NXNYDownloadError(ValueError):
    """An error identified by fixed codes safe to emit in production logs."""


class NonPDFReport(Exception):
    """The detail page explicitly identifies an attachment as non-PDF."""


def declared_report_format(text):
    match = re.search(r"格式\s*[：:]\s*\.?([A-Za-z0-9]{1,12})(?![A-Za-z0-9])", text)
    return match[1].lower() if match else None


def save_ignored_ids(ignored):
    path = ROOT / "ignored_nxny_ids.json"
    with tempfile.NamedTemporaryFile(dir=ROOT, prefix=".ignored-nxny-", delete=False, mode="w", encoding="utf-8") as output:
        json.dump(ignored, output, sort_keys=True, indent=2)
        output.write("\n")
    os.replace(output.name, path)


def ignore_non_pdf(ignored, rid, extension):
    ignored[rid] = extension
    print(f"Skipped non-PDF report {rid} (format={extension})")


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
            fields="nextPageToken,files(id,name,md5Checksum,sha256Checksum,size,modifiedTime,appProperties)",
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


def parse_listing(body, *, ignored=None):
    """Return only confirmed positive-page PDFs. A valid all-Excel page is empty."""
    items = {}
    found_rows = False
    for block in re.findall(r"<li\b[^>]*>(.*?)</li>", body, re.S | re.I):
        if not re.search(r"""class\s*=\s*["'][^"']*\brlist\b""", block, re.I):
            continue
        for anchor in re.findall(r"<a\b[^>]*>", block, re.I):
            link = re.search(r"""href\s*=\s*["']/report/view_(\d+)\.html["']""", anchor, re.I)
            title_attr = re.search(r"""(?:^|\s)title\s*=\s*(["'])(.*?)\1""", anchor, re.I | re.S)
            if link and title_attr:
                break
        else:
            continue
        found_rows = True
        rid, title = link[1], html.unescape(title_attr[2])
        labels = html.unescape(re.sub(r"<[^>]*>", " ", block))
        icon = re.search(r"""<img\b[^>]*\bsrc\s*=\s*["'](?:[^"']*/)?hz/([a-z0-9]+)\.gif["']""", block, re.I)
        fmt = declared_report_format(labels)
        extension = non_pdf_suffix(title)
        if fmt and fmt != "pdf":
            extension = fmt
        if icon and icon[1].lower() != "pdf":
            extension = icon[1].lower()
        if extension:
            if ignored is not None and rid not in ignored:
                ignore_non_pdf(ignored, rid, extension)
            continue
        pages = re.search(r"页数\s*[：:]\s*(\d+)", labels)
        if not icon or not pages or int(pages[1]) < 1:
            continue
        try:
            day = datetime.strptime(title.rsplit("-", 1)[-1], "%y%m%d").date()
        except ValueError:
            day = None
        uploaded = re.search(r"上传时间\s*[：:]\s*(\d{8})\b", labels)
        try:
            upload_day = datetime.strptime(uploaded[1], "%Y%m%d").date() if uploaded else None
        except ValueError:
            upload_day = None
        items[rid] = (title, day, upload_day, int(pages[1]))
    if not found_rows:
        raise ValueError("NXNY list unavailable or structure changed")
    return items


def scan_listing(today, seen_ids, archived_names, limit, ignored, target="", days=7):
    """Discover both channels using public listing pages and one shared PDF budget."""
    cutoff = today - timedelta(days=days)
    seen_ids = set(seen_ids)
    max_pages = MAX_LIST_PAGES * ((days + 6) // 7)
    candidates = {"qh": [], "hc": []}
    issues = []
    scanned = 0
    for channel, reports in candidates.items():
        fingerprints = set()
        # Leave room for a cross-listed 广发 issue even when the bond queue is full.
        capacity = limit if channel == "qh" else max(1, limit - len(candidates["qh"]))
        for page in range(1, max_pages + 1):
            suffix = "" if page == 1 else f"_p{page}"
            url = f"{LIST_BASE}/stype_{channel}{suffix}/"
            scanned += 1
            try:
                response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=45)
                response.raise_for_status()
                body = response.content.decode("utf8")
                items = parse_listing(body, ignored=ignored)
            except (requests.RequestException, ValueError) as error:
                issues.append(f"{channel} page {page}: {type(error).__name__}")
                break
            fingerprint = frozenset(re.findall(r"/report/view_(\d+)\.html", body, re.I))
            if fingerprint in fingerprints:
                issues.append(f"{channel} page {page}: repeated listing")
                break
            fingerprints.add(fingerprint)
            for rid, (title, report_day, _, pages) in items.items():
                if rid in seen_ids or rid in ignored or (target and rid != target):
                    continue
                if not report_day or not cutoff <= report_day <= today:
                    continue
                if not weekly_broker(title, pages=pages, channel=channel):
                    continue
                if f"{rid}-{name_for(title)}.pdf" in archived_names:
                    continue
                reports.append((rid, title, pages, channel))
                seen_ids.add(rid)
                if len(reports) == capacity:
                    break
            if len(reports) == capacity or (target and target in seen_ids):
                break
            uploads = [upload for _, _, upload, _ in items.values()]
            if uploads and all(upload and upload < cutoff for upload in uploads):
                break
            next_pages = {int(n) for n in re.findall(
                rf"""href\s*=\s*["'](?:https://www\.nxny\.com)?/stype_{channel}_p(\d+)/["']""", body
            ) if int(n) > page}
            if not next_pages:
                break
            if page + 1 not in next_pages:
                issues.append(f"{channel} page {page}: next page missing")
                break
        else:
            issues.append(f"{channel}: list page limit reached before completing discovery")
        if target and target in seen_ids:
            break
    hc = candidates["hc"]
    return candidates["qh"][:limit - len(hc)] + hc, scanned, ", ".join(issues) or None


def nxny_download_link(hrefs):
    pattern = r"^https://(vip\d+)\.nxny\.com/download/download\.aspx(?:\?|$)"
    controls = {url: match[1].lower() for url in hrefs if (match := re.match(pattern, url, re.I))}
    links = [url for url, host in controls.items() if host == "vip6"]
    if len(links) != 1:
        other_count = len(controls) - len(links)
        raise NXNYDownloadError(f"download_link_count={len(links)} alternate_node_link_count={other_count}")
    return links[0]


def nxny_response_code(data):
    # Check explicitly worded limit messages. Generic HTML or 429 alone cannot
    # distinguish quota exhaustion from other conditions.
    sample = bytes(data[:65536])
    for encoding in ("utf-8", "gb18030"):
        message = html.unescape(sample.decode(encoding, errors="ignore"))
        message = re.sub(r"\s+", "", re.sub(r"<[^>]*>", "", message))
        if re.search(r"(?:下载次数|下载额度|下载数量).{0,18}(?:已达上限|达到上限|已用完|已耗尽)|(?:已达|达到|超过).{0,12}(?:每日|今日|当天).{0,10}下载上限", message):
            return "non_pdf_response reason=quota_message"
        if re.search(r"下载.{0,10}(?:过于频繁|间隔太短)|请.{0,15}(?:秒后|分钟后).{0,12}(?:再次下载|重试)", message):
            return "non_pdf_response reason=cooldown_message"
    return "non_pdf_response"


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
            extension = declared_report_format(page.locator("body").inner_text(timeout=15000))
            if extension is None:
                raise NXNYDownloadError("detail_format_missing")
            if extension != "pdf":
                raise NonPDFReport(extension)
            page.locator("#loginstr2").filter(has_text="退出登录").wait_for(timeout=15000)
            hrefs = page.locator("a[href]").evaluate_all("els => els.map(a=>a.href)")
            link = nxny_download_link(hrefs)
            cdp = context.new_cdp_session(page)
            frame = cdp.send("Page.getFrameTree")["frameTree"]["frame"]["id"]
            result = cdp.send("Network.loadNetworkResource", {"frameId":frame, "url":link,
                    "options":{"disableCache":True,"includeCredentials":True}})["resource"]
            if not result.get("success") or not result.get("stream"):
                status = result.get("httpStatusCode")
                if status in (401, 403, 429):
                    raise NXNYDownloadError(f"download_http_status={int(status)}")
                raise NXNYDownloadError("download_stream_unavailable")
            handle = result["stream"]
            pdf = bytearray()
            try:
                while True:
                    part = cdp.send("IO.read", {"handle":handle,"size":131072})
                    pdf.extend(base64.b64decode(part["data"]) if part.get("base64Encoded") else part["data"].encode())
                    if len(pdf) >= MAX_BYTES:
                        raise NXNYDownloadError("pdf_size_limit")
                    if part.get("eof"):
                        break
            finally:
                try:
                    cdp.send("IO.close", {"handle":handle})
                except Exception:
                    print("::warning::NXNY download stream cleanup failed")
            if not pdf.startswith(b"%PDF-"):
                raise NXNYDownloadError(nxny_response_code(pdf))
            return bytes(pdf)
    finally:
        try:
            requests.post(api+"/cancel", headers=auth, json={"session_id":session["session_id"]},
                          timeout=45).raise_for_status()
        except Exception:
            print("::warning::NXNY browser session cleanup failed")


def name_for(title):
    title = title.translate(str.maketrans({":": "：", "|": "｜"}))
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", title).strip(" .")
    if len(name) > 80 and re.search(r"-\d{6}$", name):
        return name[:58] + "…" + name[-21:]
    return name[:80]


def recover_history(drive, cutoff, today, skip_ids, published, limit, *, failures=None):
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
        if not stamp:
            continue
        try:
            if not cutoff <= datetime.strptime(stamp[1], "%y%m%d").date() <= today:
                continue
            row = files.get(fileId=report["pdf_id"], fields="id,size,md5Checksum,trashed",
                            supportsAllDrives=True).execute(num_retries=2)
            if row.get("trashed"):
                if failures is not None:
                    failures.append("Drive catalog PDF: trashed")
                continue
            raw = download_cached(drive, row)
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("Historical catalog digest mismatch")
            prefix = rid + "-" if rid.isdigit() else ""
            save_recovered(f"{prefix}{name_for(title)}.pdf", raw)
        except HttpError as exc:
            if exc.resp.status == 404:
                if failures is not None:
                    failures.append("Drive catalog PDF: unavailable")
                continue
            if failures is None:
                raise
            failures.append(f"Drive catalog PDF: {type(exc).__name__}")
            continue
        except Exception as exc:
            if failures is None:
                raise
            failures.append(f"Drive catalog PDF: {type(exc).__name__}")
            continue
        if rid.isdigit():
            skip_ids.add(rid)
        published.add(digest)
        count += 1
        if limit is not None and count >= limit:
            break
    return count


def save_recovered(filename, raw):
    path = INBOX / filename
    if path.exists() and path.read_bytes() != raw:
        raise ValueError("Conflicting archive filenames")
    path.write_bytes(raw)


def fetch_drive_only(drive, folder):
    INBOX.mkdir(exist_ok=True)
    anchored = ROOT / "anchored_reports.json"
    known = {row["report_id"] for row in json.loads(anchored.read_bytes())} if anchored.exists() else set()
    today = datetime.now(CN).date()
    cutoff = today - timedelta(days=56)
    manuals, weekly, failures = [], [], []
    for row in list_pdfs(drive, folder):
        name = row["name"]
        try:
            if manual_identity(name):
                manuals.append(row)
            elif weekly_broker(name) and (stamp := re.search(r"-(\d{6})\.pdf$", name, re.I)):
                if cutoff <= datetime.strptime(stamp[1], "%y%m%d").date() <= today:
                    weekly.append(row)
        except ValueError:
            failures.append("Drive filename: invalid_report_date")

    counts = []
    for rows in (manuals, weekly):
        recovered = 0
        for row in rows:
            checksums = {row.get("sha256Checksum"), row.get("appProperties", {}).get("sha256")} - {None, ""}
            try:
                if len(checksums) > 1:
                    raise ValueError("Conflicting Drive checksums")
                if checksums & known:
                    continue
                raw = download_cached(drive, row)
                actual = hashlib.sha256(raw).hexdigest()
                if checksums and actual not in checksums:
                    raise ValueError("Drive SHA-256 mismatch")
                if actual in known:
                    continue
                if any(c in row["name"] for c in "/\\"):
                    raise ValueError("Unsafe Drive filename")
                save_recovered(row["name"], raw)
                known.add(actual)
                recovered += 1
            except Exception as exc:
                failures.append(f"Drive folder PDF: {type(exc).__name__}")
        counts.append(recovered)
    historical = 0
    try:
        historical = recover_history(drive, cutoff, today, set(), known, None, failures=failures)
    except Exception as exc:
        failures.append(f"Drive catalog: {type(exc).__name__}")
    print(f"Drive only: manual={counts[0]}, folder_weeklies={counts[1]}, catalog={historical}, NXNY requests=0")
    if failures:
        print("::warning::Drive recovery incomplete: " + ", ".join(failures))
        return 2
    return 0


def fetch(drive, folder, *, archive_only=False, drive_only=False):
    target = os.environ.get("NXNY_REPORT_ID", "")
    INBOX.mkdir(exist_ok=True)
    (INBOX / "listing.json").unlink(missing_ok=True)
    if drive_only:
        if target or archive_only:
            raise ValueError("Drive-only batch cannot be combined with a report ID or archive-only lookup")
        return fetch_drive_only(drive, folder)
    if (target or archive_only) and not re.fullmatch(r"[0-9]{1,12}", target):
        raise ValueError("NXNY report ID must be numeric")
    ignored = json.loads((ROOT / "ignored_nxny_ids.json").read_bytes())
    if target in ignored:
        print(f"Skipped previously confirmed non-PDF report {target} (format={ignored[target]})")
        return 0
    INBOX.mkdir(exist_ok=True)
    records = json.loads((ROOT/"dashboard_data.json").read_text("utf8"))["records"]
    anchored = ROOT/"anchored_reports.json"
    anchored_rows = json.loads(anchored.read_bytes()) if anchored.exists() else []
    anchored_hashes = {r["report_id"] for r in anchored_rows}
    days = int(os.environ.get("REPORT_LOOKBACK_DAYS") or "7")
    if not 1 <= days <= 56:
        raise ValueError("REPORT_LOOKBACK_DAYS must be in 1..56")
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
            if not match or not 0 <= (datetime.now(CN).date() - datetime.strptime(match[1], "%y%m%d").date()).days <= days:
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
    skip_ids = published_ids | set(by_id) | ignored.keys()
    if days > 7 and not target and recovered < limit:
        today = datetime.now(CN).date()
        recovered += recover_history(drive, today - timedelta(days=days), today, skip_ids, set(known), limit - recovered)
    reports, pages = [], 0
    if recovered < limit and not archive_only and (not target or target not in by_id):
        previous_ignored_count = len(ignored)
        reports, pages, issue = scan_listing(datetime.now(CN).date(), skip_ids, prior, limit - recovered, ignored, target, days)
        if len(ignored) != previous_ignored_count:
            save_ignored_ids(ignored)
        if issue:
            failures.append("discovery: " + issue)
        if target and not reports and target not in published_ids and target not in ignored:
            failures.append("Selected report is absent from the eligible listing")
    downloaded = 0
    listing = {}
    for rid, title, report_pages, channel in reports:
        filename = f"{rid}-{name_for(title)}.pdf"
        if rid in by_id or filename in prior:
            continue
        if downloaded >= limit:
            break
        try:
            (INBOX/filename).write_bytes(fetch_nxny(rid))
            listing[filename] = {"title": title, "pages": report_pages, "channel": channel}
            downloaded += 1
        except NonPDFReport as skipped:
            ignore_non_pdf(ignored, rid, str(skipped))
            save_ignored_ids(ignored)
            continue
        except Exception as error:
            downloaded += 1
            reason = str(error) if isinstance(error, NXNYDownloadError) else type(error).__name__
            failures.append(f"{rid}: {reason}")
            # An expired browser session should consume at most one attempted download.
            break
    if listing:
        (INBOX / "listing.json").write_text(json.dumps(listing, ensure_ascii=False), encoding="utf8")
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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--archive-only", action="store_true")
    mode.add_argument("--drive-only", action="store_true")
    args = parser.parse_args()
    if (args.archive_only or args.drive_only) and args.action != "fetch":
        parser.error("Archive recovery modes are only available for fetch")
    try:
        drive, folder = drive_service(), env("GDRIVE_FOLDER_ID")
        if args.action == "fetch":
            sys.exit(fetch(drive, folder, archive_only=args.archive_only, drive_only=args.drive_only))
        sys.exit(backup(drive, folder))
    except Exception as error:
        # Remote exceptions can contain signed URLs; log class and stage only.
        print(f"::error::{args.action} failed ({type(error).__name__}). Check configuration and provider status.")
        sys.exit(1)
