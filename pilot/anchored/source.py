"""S0. Read every PDF page; never shorten text to fit a model context."""
import hashlib
import io
import re
from collections import Counter
from pypdf import PdfReader

def index_pdf(data):
    reader=PdfReader(io.BytesIO(data))
    paragraphs=[]
    diagnostics=[]
    heading=""
    outline={}
    def bookmarks(nodes):
        for node in nodes:
            if isinstance(node,list):
                bookmarks(node)
            else:
                try:
                    num=reader.get_destination_page_number(node)
                    if num is not None and num>=0:
                        outline.setdefault(num,[]).append(str(node.title))
                except (KeyError,ValueError,TypeError):
                    continue
    bookmarks(reader.outline)
    for page_no,page in enumerate(reader.pages,1):
        fonts={}
        sizes=Counter()
        def remember(text,cm,tm,font,size):
            for line in text.splitlines():
                if line.strip():
                    fonts.setdefault(line.strip(),[]).append(float(size))
                    sizes[float(size)]+=len(line.strip())
        raw=page.extract_text(visitor_text=remember) or ""
        body=sizes.most_common(1)[0][0] if sizes else 0
        serial=0
        chunks=[]
        def flush():
            nonlocal serial
            if chunks:
                serial+=1
                paragraphs.append(dict(pid=f"P{page_no}-S{serial}",page=page_no,
                    text="\n".join(chunks),heading=heading))
                chunks.clear()
        for line in raw.splitlines():
            clean=line.strip()
            if not clean:
                flush()
                continue
            # Layout cues only. They annotate headings and never filter text.
            is_heading=(clean in outline.get(page_no-1,[]) or
                        (body and len(clean)<=80 and min(fonts.get(clean,[0]))>body*1.16))
            if is_heading:
                flush()
                heading=clean
            chunks.append(line)
            if is_heading or re.search(r"[。！？；][”’）)]?$",clean):
                flush()
        flush()
        resources=page.get("/Resources")
        objects=(resources.get_object().get("/XObject") if resources else None)
        has_images=bool(objects and any(ref.get_object().get("/Subtype")=="/Image" for ref in objects.get_object().values()))
        diagnostics.append(dict(page=page_no,text_chars=len(raw),paragraphs=serial,has_image=has_images,
                                text_missing=not raw.strip()))
    return dict(report_id=hashlib.sha256(data).hexdigest(),paragraphs=paragraphs,pages=diagnostics)

def context(index,pids):
    rows=index["paragraphs"]
    pos={p["pid"]:i for i,p in enumerate(rows)}
    selected=set()
    for pid in pids:
        i=pos[pid]
        selected.update(range(max(0,i-1),min(len(rows),i+2)))
    return [rows[i] for i in sorted(selected)]

