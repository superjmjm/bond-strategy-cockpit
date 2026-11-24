"""Cross-check report references against the local PDFs before publication."""

import argparse
import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path

from pypdf import PdfReader

if __package__:
    from .report_scope import weekly_broker
else:
    from report_scope import weekly_broker

ROOT = Path(__file__).resolve().parents[1]
TENORS = {"1-3Y", "5-7Y", "10Y", "30Y", "curve"}


def normalized(text):
    # Keep punctuation and numbers, normalizing full-width forms and PDF line wraps.
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).replace("\u00ad", "")


def citation_spans(path):
    """Index exact, contiguous source phrases with their physical page numbers."""
    spans = {}
    for page, sheet in enumerate(PdfReader(path).pages, start=1):
        text = re.sub(r"\s+", " ", sheet.extract_text()).strip()
        index = 0
        for sentence in re.findall(r"[^。！？；]+[。！？；]?", text):
            phrases = [sentence]
            if len(normalized(sentence)) > 100:
                phrases = re.findall(r"[^，、]+[，、]?", sentence)
            for phrase in phrases:
                for start in range(0, len(phrase), 100):
                    quote = phrase[start:start + 100].strip()
                    if len(normalized(quote)) >= 6:
                        index += 1
                        spans[f"P{page}S{index}"] = (page, quote)
    return spans


def report_identity(rows, *, require_quote=False):
    if len(rows) != len(TENORS) or {row["tenor"] for row in rows} != TENORS:
        raise ValueError("Report needs a complete set of tenor views")
    names = {row["source_file"] for row in rows}
    digests = {row["report_id"] for row in rows}
    if len(names) != 1 or len(digests) != 1:
        raise ValueError("Views disagree on their source document")
    name = names.pop()
    digest = digests.pop()
    if not isinstance(name, str) or "/" in name or chr(92) in name or not name.endswith(".pdf"):
        raise ValueError("Unexpected source filename")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Unexpected report SHA-256")
    broker = weekly_broker(name)
    if not broker or any(row["broker"] != broker for row in rows):
        raise ValueError("Views disagree with the covered broker")
    if len({(row["date"], row["report_title"]) for row in rows}) != 1:
        raise ValueError("Views disagree on the reporting date or title")
    quotes = []
    for row in rows:
        if row["stance"] == "unstated":
            if row["evidence_quote"] or row["evidence_page"] is not None:
                raise ValueError("An unstated view cannot include a quotation")
        else:
            quotes.append(row)
    if require_quote and not quotes:
        raise ValueError("Selected report contains no stated China Treasury view")
    return name, digest, quotes


def verify_pdf(path, digest, quotes):
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
            raise ValueError("PDF hash does not match dashboard data")
    reader = PdfReader(path)
    page_text = {}
    for row in quotes:
        page, quote = row["evidence_page"], normalized(row["evidence_quote"])
        if not isinstance(page, int) or not 1 <= page <= len(reader.pages) or not 1 <= len(quote) <= 100:
            raise ValueError(f"Invalid evidence for {row['tenor']}")
        if page not in page_text:
            sheet = reader.pages[page - 1]
            page_text[page] = [normalized(sheet.extract_text()), normalized(sheet.extract_text(extraction_mode="layout"))]
        if not any(quote in variant for variant in page_text[page]):
            raise ValueError(f"Quote mismatch for {row['tenor']} on PDF page {page}")
        print(f"PDF evidence verified: {row['tenor']} page {page}")
    print(f"PDF verified: {len(reader.pages)} pages, {len(quotes)} quoted views")


def verify(target, inbox, data):
    if not re.fullmatch(r"[0-9]{1,12}", target):
        raise ValueError("Invalid selected report ID")
    if data["demo_mode"]:
        raise ValueError("No live dataset produced for the selected report")
    rows = [
        row for row in data["records"]
        if not row["is_demo"] and row["source_file"].startswith(target + "-")
    ]
    name, digest, quotes = report_identity(rows, require_quote=True)
    files = [path for path in inbox.glob("*.pdf") if path.is_file()]
    if not files:
        print("Selected report already present in the dashboard")
        return
    if len(files) != 1 or files[0].name != name:
        raise ValueError("Inbox contains a PDF outside the manual selection")
    verify_pdf(files[0], digest, quotes)


def verify_new_reports(before, after, inbox):
    old_ids = {row["report_id"] for row in before["records"] if not row["is_demo"]}
    published = [row for row in after["records"] if not row["is_demo"]]
    if not old_ids <= {row["report_id"] for row in published}:
        raise ValueError("A report refresh removed previously published records")
    for key in ("benchmark_yields", "benchmark_as_of", "benchmark_note", "benchmark_source_url"):
        if key in before and before[key] != after.get(key):
            raise ValueError("A report refresh changed benchmark data")
    new = {}
    for row in published:
        if row["report_id"] not in old_ids:
            new.setdefault(row["report_id"], []).append(row)
    for rows in new.values():
        name, digest, quotes = report_identity(rows)
        verify_pdf(inbox / name, digest, quotes)
    print(f"Pre-publication PDF verification passed for {len(new)} new reports.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--previous", type=Path, help="Verify every new report against the previous dashboard")
    args = parser.parse_args()
    with (ROOT / "dashboard_data.json").open(encoding="utf-8-sig") as stream:
        data = json.load(stream)
    if args.previous:
        with args.previous.open(encoding="utf-8-sig") as stream:
            verify_new_reports(json.load(stream), data, ROOT / "reports_inbox")
    else:
        verify(os.environ["NXNY_REPORT_ID"], ROOT / "reports_inbox", data)
