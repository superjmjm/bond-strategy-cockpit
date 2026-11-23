"""Cross-check the selected report against the local PDF before a manual publication."""

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


def verify(target, inbox, data):
    if not re.fullmatch(r"[0-9]{1,12}", target):
        raise ValueError("Invalid selected report ID")
    if data["demo_mode"]:
        raise ValueError("No live dataset produced for the selected report")
    records = [row for row in data["records"] if not row["is_demo"]]
    rows = [row for row in records if row["source_file"].startswith(target + "-")]
    if len(rows) != len(TENORS) or {row["tenor"] for row in rows} != TENORS:
        raise ValueError("Selected report needs a complete set of tenor views")
    names = {row["source_file"] for row in rows}
    digests = {row["report_id"] for row in rows}
    if len(names) != 1 or len(digests) != 1:
        raise ValueError("Selected views disagree on their source document")
    name = names.pop()
    broker = weekly_broker(name)
    if not broker or any(row["broker"] != broker for row in rows):
        raise ValueError("Selected views disagree with the covered broker")

    quotes = [row for row in rows if row["stance"] != "unstated"]
    if not quotes:
        raise ValueError("Selected report contains no stated China Treasury view")
    files = [path for path in inbox.glob("*.pdf") if path.is_file()]
    if not files:
        # The fetch step leaves the inbox empty on a repeat of a published selection.
        print("Selected report already present in the dashboard")
        return
    if len(files) != 1 or files[0].name != name:
        raise ValueError("Inbox contains a PDF outside the manual selection")
    path = files[0]
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != digests.pop():
            raise ValueError("Selected PDF hash does not match dashboard data")
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
    print(f"Selected PDF verified: {len(reader.pages)} pages, {len(quotes)} quoted views")


if __name__ == "__main__":
    with (ROOT / "dashboard_data.json").open(encoding="utf-8-sig") as stream:
        verify(os.environ["NXNY_REPORT_ID"], ROOT / "reports_inbox", json.load(stream))
