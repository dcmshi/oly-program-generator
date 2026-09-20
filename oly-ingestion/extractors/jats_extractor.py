# extractors/jats_extractor.py
"""
Europe PMC full-text XML (JATS) → plain text for the ingestion pipeline.

Open-access papers in PMC are served as JATS XML by
`https://www.ebi.ac.uk/europepmc/webservices/rest/<PMCID>/fullTextXML`, which
is cleaner than any PDF extraction (no columns, running heads or hyphenation)
and needs no publisher download. `jats_to_text` renders the body as markdown-
style headings (`#`, `##`, …) with `\\n\\n` between paragraphs — what the
classifier and chunker expect — keeps table cells tab-separated so numbers
stay attached to their row, and drops the reference list, author notes and
figures (their captions are kept).

    python -m extractors.jats_extractor PMC8582352 "sources/research/Travis 2021 - taper.txt"
"""

import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

EUROPEPMC_FULLTEXT_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
_DROP_TAGS = {"ref-list", "xref", "fn-group", "author-notes", "back", "graphic", "inline-graphic", "alternatives",
              "supplementary-material", "table-wrap-foot", "disp-formula", "inline-formula"}


def fetch_jats(pmcid: str, timeout: int = 120, attempts: int = 4) -> str:
    """Europe PMC answers 503 under load; retry with backoff before giving up."""
    import time
    for attempt in range(attempts):
        resp = requests.get(EUROPEPMC_FULLTEXT_URL.format(pmcid=pmcid), timeout=timeout)
        if resp.status_code < 500:
            resp.raise_for_status()
            return resp.text
        time.sleep(3 * (attempt + 1))
    resp.raise_for_status()
    return resp.text


def _text(el: ET.Element) -> str:
    """Inline text of an element, xrefs (citation markers) removed."""
    parts = [el.text or ""]
    for child in el:
        if child.tag not in _DROP_TAGS:
            parts.append(_text(child))
        parts.append(child.tail or "")
    text = re.sub(r"[ \t\r\n]+", " ", "".join(parts))
    # citation markers were xrefs: "[1,2]" leaves "[,]" / "[]" / "[–]" behind
    text = re.sub(r"\s*[\[(][\s,;–-]*[\])]", "", text)
    text = re.sub(r"(\s*,)+(?=\s*[.;:)])", "", text)     # bare "1, 2." markers → " , ."
    text = re.sub(r"\s+([.,;:)])", r"\1", text)
    return text.strip()


def _table(tw: ET.Element) -> list[str]:
    out = []
    label = tw.find("label")
    caption = tw.find("caption")
    head = " ".join(x for x in (_text(label) if label is not None else "", _text(caption) if caption is not None else "") if x)
    if head:
        out.append(head)
    for row in tw.iter("tr"):
        cells = [_text(c) for c in row if c.tag in ("td", "th")]
        if any(cells):
            out.append("\t".join(cells))
    return out


def _walk(el: ET.Element, depth: int, out: list[str]) -> None:
    for child in el:
        tag = child.tag
        if tag in _DROP_TAGS:
            continue
        if tag == "sec":
            title = child.find("title")
            if title is not None and _text(title):
                out.append(f"{'#' * min(depth, 4)} {_text(title)}")
            _walk(child, depth + 1, out)
        elif tag == "title" and depth == 1:
            continue
        elif tag == "p":
            # a paragraph may embed a table or list; render those after the text
            txt = _text(child)
            if txt:
                out.append(txt)
            for tw in child.iter("table-wrap"):
                out.extend(_table(tw))
        elif tag == "table-wrap":
            out.extend(_table(child))
        elif tag == "list":
            for item in child.iter("list-item"):
                t = _text(item)
                if t:
                    out.append(f"- {t}")
        elif tag == "fig":
            cap = child.find("caption")
            if cap is not None and _text(cap):
                out.append(_text(cap))
        elif tag in ("boxed-text", "disp-quote", "abstract", "body", "front", "article-meta"):
            _walk(child, depth, out)


def jats_to_text(xml: str) -> str:
    root = ET.fromstring(xml)
    for el in root.iter():                       # strip namespaces
        el.tag = el.tag.split("}", 1)[-1]
    out: list[str] = []
    meta = root.find(".//article-meta")
    if meta is not None:
        title = meta.find(".//article-title")
        if title is not None:
            out.append(f"# {_text(title)}")
        for abstract in meta.findall("abstract"):
            out.append("## Abstract")
            _walk(abstract, 3, out)
    body = root.find(".//body")
    if body is not None:
        _walk(body, 2, out)
    return "\n\n".join(out).strip() + "\n"


if __name__ == "__main__":
    pmcid, dest = sys.argv[1], Path(sys.argv[2])
    dest.write_text(jats_to_text(fetch_jats(pmcid)), encoding="utf-8")
    print(f"{pmcid} → {dest} ({dest.stat().st_size:,} bytes)")
