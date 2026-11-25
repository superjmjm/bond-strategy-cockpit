"""Ordered PDF text, physical pages and inherited section paths."""
import hashlib
import io
import re
from collections import Counter
from pypdf import PdfReader


def key(text):
    return re.sub(r"\s+", "", text)


def section_level(line):
    if len(line) > 85 or re.search(r"[。！？；]|[.·…]{3,}", line):
        return None
    if re.match(r"^[一二三四五六七八九十]+[、.．]", line):
        return 1
    if re.match(r"^[（(][一二三四五六七八九十]+[)）]", line):
        return 2
    match = re.match(r"^(\d+(?:\.\d+)*)(?:[.、．]\s*|\s+)(?=[^\d%])", line)
    return len(match[1].split(".")) if match else None


def bookmark_paths(reader):
    pages = {}
    def visit(nodes, parents=()):
        current = parents
        for node in nodes:
            if isinstance(node, list):
                visit(node, current)
                continue
            current = parents + (str(node.title).strip(),)
            page = reader.get_destination_page_number(node)
            if page is not None and page >= 0:
                pages.setdefault(page, []).append(current)
    visit(reader.outline)
    return pages


def index_pdf(data):
    reader = PdfReader(io.BytesIO(data))
    outlines = bookmark_paths(reader)
    pages, diagnostics = [], []
    margin_counts = Counter()
    for number, page in enumerate(reader.pages, 1):
        fonts, sizes = {}, Counter()
        def remember(text, cm, tm, font, size):
            for line in text.splitlines():
                label = key(line)
                if label:
                    fonts[label] = max(fonts.get(label, 0), float(size))
                    sizes[float(size)] += len(label)
        raw = page.extract_text(visitor_text=remember) or ""
        lines = raw.splitlines()
        nonempty = [key(line) for line in lines if key(line)]
        margin_counts.update(set(nonempty[:3] + nonempty[-2:]))
        pages.append((lines, fonts, sizes.most_common(1)[0][0] if sizes else 0))
        resources = page.get("/Resources")
        objects = resources.get_object().get("/XObject") if resources else None
        has_image = bool(objects and any(ref.get_object().get("/Subtype")=="/Image"
                         for ref in objects.get_object().values()))
        diagnostics.append(dict(page=number, text_chars=len(raw), has_image=has_image,
                                text_missing=not raw.strip()))
    paragraphs, path = [], []
    for number, (lines, fonts, body) in enumerate(pages, 1):
        paths = outlines.get(number-1, [])
        titles = {key(p[-1]):p for p in paths}
        # A bookmark may start below a page header. Apply it at the printed title.
        if paths and not any(key(line) in titles for line in lines):
            path = list(paths[0])
        serial, chunks = 0, []
        def flush():
            nonlocal serial
            if chunks:
                serial += 1
                paragraphs.append(dict(pid=f"P{number}-S{serial}", page=number,
                                       text="\n".join(chunks), heading=" / ".join(path) or "正文（尚无章节标题）"))
                chunks.clear()
        for line in lines:
            clean, label = line.strip(), key(line)
            if not clean:
                flush()
                continue
            level = section_level(clean)
            larger = (body and fonts.get(label, 0) > 1.15*body and len(label) <= 65
                      and not re.search(r"[。！？；]|[.·…]{3,}", clean))
            if label in titles:
                flush()
                path = list(titles[label])
            elif (level or larger) and margin_counts[label] < 3:
                flush()
                depth = level or (2 if path else 1)
                path = path[:depth-1] + [clean]
            elif re.match(r"^[⚫●■◆❖•]|^\s*[（(]\d+[)）]", clean):
                flush()
            chunks.append(line)
            if label in titles or ((level or larger) and margin_counts[label] < 3):
                flush()
        flush()
        diagnostics[number-1]["paragraphs"] = serial
    return dict(report_id=hashlib.sha256(data).hexdigest(), paragraphs=paragraphs, pages=diagnostics)


def context(index,pids):
    rows=index["paragraphs"]
    pos={p["pid"]:i for i,p in enumerate(rows)}
    selected=set()
    for pid in pids:
        i=pos[pid]
        selected.update(range(max(0,i-1),min(len(rows),i+2)))
    return [rows[i] for i in sorted(selected)]
