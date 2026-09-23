# ingest_web.py
"""
Ingest web articles into the pipeline.

Three sources are supported via --site:
  * catalyst (default) — Catalyst Athletics; crawls live category pages.
  * urls                — a curated list of article URLs on any site
                          (sources/url_lists/<name>.json, see load_url_list);
                          used for Stronger by Science, JTS / Max Aita and the
                          archived Pendlay beginner program (CORPUS.md rows 2,
                          5, 6). Generic WordPress-style extraction.
  * charniga            — Andrew "Bud" Charniga's Sportivny Press essays.
                          sportivnypress.com is defunct (domain no longer
                          resolves after his death in Jan 2025), so articles
                          are recovered from the Internet Archive Wayback
                          Machine. The essays were freely, publicly published
                          ("viewable without password"); this is HTML, so no
                          OCR is involved.

Both fetch each article, extract clean text, and ingest through the same
chunker/classifier/vector_loader stack as PDF/EPUB sources. Each article gets
its own source record (type='website').

Progress is tracked per-site in sources/{catalyst,charniga}_progress.json —
re-running skips already-ingested URLs. Chunk-level dedup (content_hash) also
prevents re-embedding identical content.

Usage:
    python ingest_web.py                        # all Catalyst priority categories
    python ingest_web.py --categories technique program_design
    python ingest_web.py --limit 20             # cap for testing
    python ingest_web.py --dry-run              # collect URLs, no ingestion
    python ingest_web.py --site charniga --dry-run   # enumerate Wayback URLs
    python ingest_web.py --site charniga             # ingest Charniga essays
    python ingest_web.py --site urls --url-file sources/url_lists/sbs.json
"""

import argparse
import json
import logging
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from config import Settings
from extractors.html_extractor import block_text
from loaders.structured_loader import StructuredLoader
from loaders.vector_loader import VectorLoader
from processors.chunker import SemanticChunker
from processors.classifier import ContentClassifier
from processors.principle_extractor import PrincipleExtractor
from processors.progress import Progress
from processors.section_processor import (
    SectionProcessor,
    SectionTarget,
    new_section_stats,
    rollback_loaders,
    run_quarantine_pass,
)

from shared.llm import light_model_for

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BASE_URL = "https://www.catalystathletics.com"
PROGRESS_FILE = Path(__file__).parent / "sources" / "catalyst_progress.json"
_CATALYST_PAGE_SIZE = 10  # Catalyst article listings paginate by 10 (?start=)

# Categories selected for relevance — skipping Editorial, Mental/Emotional,
# Equipment, Interviews, and paywalled Training Programs
CATALYST_CATEGORIES = {
    "technique": {
        "section": 17,
        "name": "Olympic Weightlifting Technique",
    },
    "program_design": {
        "section": 13,
        "name": "Weightlifting Program Design",
    },
    "training": {
        "section": 18,
        "name": "Olympic Weightlifting Training",
    },
    "competition": {
        "section": 14,
        "name": "Weightlifting Competition",
    },
    "general": {
        "section": 19,
        "name": "Olympic Weightlifting General",
    },
    "recovery": {
        "section": 10,
        "name": "Mobility, Prep, Recovery & Injury",
    },
}

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; OlyIngestionBot/1.0; research use)"
})

# ── Charniga / Sportivny Press (via Wayback Machine) ───────────
# sportivnypress.com went offline after Andrew Charniga's death (Jan 2025) —
# the domain no longer resolves. His articles were freely, publicly published,
# so they are recovered from the Internet Archive. This is HTML (no OCR).
CHARNIGA_DOMAIN = "sportivnypress.com"
CHARNIGA_PROGRESS_FILE = Path(__file__).parent / "sources" / "charniga_progress.json"
# CDX enumerates every archived URL under the domain.
# https, not http — the plain-http endpoint failed live (503/timeout) where
# https succeeded on the first attempt (audit4-F1)
WAYBACK_CDX_URL = "https://web.archive.org/cdx/search/cdx"
# The `id_` suffix returns the RAW capture (no Wayback toolbar / JS injection),
# so the parsed HTML is byte-identical to what was originally served.
WAYBACK_RAW_FMT = "https://web.archive.org/web/{timestamp}id_/{original}"

# Sportivny Press ran on WordPress (URL patterns /YYYY/slug/, /category/…).
# WordPress renders the post body in .entry-content; the fallback chain covers
# theme variations.
# TODO (DB machine): confirm the real content class against one live snapshot
# before the full run — the exact theme class may differ. Fetch e.g.
#   http://web.archive.org/web/2020id_/https://www.sportivnypress.com/2016/russian-training-part-2/
# and inspect the container that wraps the article body.
CHARNIGA_CONTENT_SELECTORS = [
    {"name": "div", "class_": re.compile(r"entry[-_]content", re.I)},
    {"name": "div", "class_": re.compile(r"post[-_]content", re.I)},
    {"name": "article"},
]
# Non-article URLs to skip: WP plumbing, taxonomy/listing pages, feeds, assets,
# and anything carrying a query string (comment/reply links, ?p=id duplicates).
_CHARNIGA_SKIP = re.compile(
    r"/(category|tag|author|page|feed|wp-content|wp-admin|wp-includes|wp-json|comments)(/|$)|"
    r"\.(jpg|jpeg|png|gif|svg|css|js|pdf|xml|ico|zip|mp4)$|"
    r"\?",
    re.I,
)
# Positive article-shape requirement (ING-M2): WordPress permalinks here are
# /YYYY/slug/ (optionally /YYYY/MM/slug/). The skip list alone let the
# homepage, bare date archives (/2016/), static pages (/about/), and
# comment-page-N pagination through to be ingested as nav soup.
# (:\d+)? — CDX originals from HTTP-era crawls carry :80; the filter runs
# before urlkey dedup, so rejecting them dropped whole essays (audit2-M2).
# (?!\d+/?$) — a purely numeric final segment is a month archive (/2016/05/)
# or a WP ?p=ID shortlink (/2014/439/) that duplicates a canonical essay
# (audit2-M1, audit4-F7). Hyphenated numeric-looking slugs (/2017-review/)
# still pass.
_CHARNIGA_ARTICLE_RE = re.compile(
    r"^https?://(www\.)?sportivnypress\.com(:\d+)?/\d{4}(/\d{2})?/(?!\d+/?$)[^/]+/?$",
    re.I,
)


# ── URL collection ─────────────────────────────────────────────

def collect_category_urls(section_id: int, section_name: str) -> list[str]:
    """Paginate through a category page and return all article URLs."""
    urls = []
    start = 0
    # Catalyst paginates in steps of 10 (?start=0,10,20,…). Kept as a named
    # constant so the offset step is discoverable if the site ever changes it (I-L10).
    page_size = _CATALYST_PAGE_SIZE

    while True:
        page_url = (
            f"{BASE_URL}/articles/section/{section_id}/{section_name.replace(' ', '-')}/"
            f"?start={start}"
        )
        try:
            resp = SESSION.get(page_url, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"Failed to fetch category page {page_url}: {e}")
            break

        soup = BeautifulSoup(resp.text, "lxml")
        # Article links follow /article/{id}/{slug}/
        page_links = [
            a["href"] for a in soup.find_all("a", href=True)
            if re.search(r"/article/\d+/", a["href"])
        ]
        # Normalise to absolute URLs, deduplicate
        page_links = list(dict.fromkeys(
            link if link.startswith("http") else BASE_URL + link
            for link in page_links
        ))

        if not page_links:
            break

        urls.extend(page_links)
        logger.info(f"  {section_name} (start={start}): found {len(page_links)} links")

        # Next page exists if any link advances `start` past the current page —
        # matching any larger offset (not exactly start+page_size) so a change in
        # the site's step doesn't silently truncate crawling (I-L10).
        has_next = any(
            (m := re.search(r"[?&]start=(\d+)", a.get("href") or "")) and int(m.group(1)) > start
            for a in soup.find_all("a", href=True)
        )
        if not has_next:
            break

        start += page_size
        time.sleep(0.5)

    return list(dict.fromkeys(urls))  # global dedupe


# ── Article header boilerplate (RAG-L11) ───────────────────────

_DATE_LINE_RE = re.compile(
    r"^(?:(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}"
    r"|\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})$"
)
_HEADER_NOISE = {"see related articles", "related articles", "share this article", "print this article",
                 "written by", "by"}


def strip_article_header(text: str, title: str = "", author: str = "") -> str:
    """Drop the page chrome that precedes an article body: the title and author
    lines (already captured as metadata), a date line, and "See Related
    Articles". Without this every Catalyst article's first chunk embedded
    `Podcasts with Greg Everett | Greg Everett | January 23, 2015 | See Related
    Articles | …` (RAG-L11). Only LEADING lines are considered; the first real
    paragraph ends the scan."""
    known = {t.strip().lower() for t in (title, author) if t and t.strip()}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        if not ln or ln.lower() in known or ln.lower() in _HEADER_NOISE or _DATE_LINE_RE.match(ln):
            i += 1
            continue
        break
    return chr(10).join(lines[i:]).strip()


# ── Article fetching & extraction ──────────────────────────────

def fetch_article(url: str) -> tuple[dict | None, bool]:
    """Fetch an article URL and return (article, permanent_skip).

    Same contract as fetch_charniga_snapshot: permanent_skip=True only for
    failures that can never succeed (404-class, no content element) — a
    transient network blip must leave the URL pending for the next run
    instead of permanently dropping the article from the corpus (audit2-L1).
    """
    resp, permanent = _get_with_retry(url, timeout=15)
    if resp is None:
        return None, permanent

    soup = BeautifulSoup(resp.text, "lxml")

    # ── Catalyst-specific: article body is in the left column ──
    main = soup.find("div", class_="sub_page_main_area_half_container_left")

    # Remove sidebar/comments/related from the left container if present
    if main:
        for tag in main(["div"], class_=re.compile(r"seealso|comments|author_bio", re.I)):
            tag.decompose()

    # Fallback chain for non-Catalyst or future sites
    if main is None:
        for tag in soup(["nav", "header", "footer", "script", "style", "aside"]):
            tag.decompose()
        main = (
            soup.find("div", class_=re.compile(r"article[-_]?(content|body|text)", re.I))
            or soup.find("article")
            or soup.find("main")
            or soup.find(id=re.compile(r"content", re.I))
            or soup.body
        )

    # ── Title — first line of the article container, or <title> tag ──
    title = ""
    if main:
        first_line = main.get_text(separator="\n", strip=True).split("\n")[0].strip()
        if len(first_line) > 5:
            title = first_line
    if not title:
        title_tag = soup.find("title")
        if title_tag:
            # Strip " - Category - Catalyst Athletics" suffix
            title = re.sub(r"\s*[-|]\s*(.*?)\s*[-|]\s*Catalyst Athletics.*$", "", title_tag.get_text(strip=True))

    # ── Author — second non-empty line in the container, or default ──
    author = "Greg Everett"
    if main:
        lines = [ln.strip() for ln in main.get_text(separator="\n", strip=True).split("\n") if ln.strip()]
        # Author line is typically the second line (after title), before the date
        if len(lines) > 1:
            candidate = lines[1]
            # Plausible author: 2-4 words, no digits, not a date
            if re.match(r"^[A-Z][a-zA-Z\s\-\.]{3,40}$", candidate) and not re.search(r"\d", candidate):
                author = candidate

    if main is None:
        logger.warning(f"No content element found for {url}")
        return None, True

    # block_text inserts \n\n paragraph markers so the chunker can split within
    # long articles (mutates `main` — must run after the title/author reads above)
    text = strip_article_header(block_text(main), title, author)

    if len(text) < 200:
        logger.warning(f"Very short article ({len(text)} chars) at {url} — skipping")
        return None, True

    return {"title": title, "author": author, "text": text, "url": url}, False


# ── Curated URL lists (any site) ───────────────────────────────

URL_LIST_PROGRESS_FILE = Path(__file__).parent / "sources" / "urls_progress.json"
URL_LIST_DIR = Path(__file__).parent / "sources" / "url_lists"
GENERIC_MIN_WORDS = 400          # video / podcast landing pages carry a blurb + teaser cards only
_GENERIC_CONTAINERS = [".entry-content", "article", "main", "#content", "#primary"]
_GENERIC_DROP = re.compile(
    r"share|social|related|comment|sidebar|newsletter|subscribe|author-box|author_bio|cookie|"
    r"breadcrumb|pagination|post-nav|jp-relatedposts|sharedaddy|promo|advert|d-none|featured-article|sticky|"
    r"lasso|aawp|affiliate|toc_container|ez-toc",
    re.I,
)
_SITE_TITLE_SUFFIX_RE = re.compile(r"\s*[-|•–]\s*[^-|•–]{2,60}$")
_BYLINE_LINE_RE = re.compile(r"^(written\s+)?by:?$", re.I)
_INLINE_BYLINE_RE = re.compile(r"^(written\s+)?by:?\s+(?P<name>.+)$", re.I)
_NAME_LINE_RE = re.compile(r"^[A-Z][a-zA-Z\-.']+(\s+[A-Z][a-zA-Z\-.']+){1,3}$")
# Page chrome that follows an article body on WordPress themes; the text is
# cut at the first of these once past _TRAILER_MIN_FRACTION of its length.
_TRAILER_RE = re.compile(
    r"^(see more in\b|related (posts|articles)|you may also like|popular products|featured articles|"
    r"leave a (reply|comment)|manage consent|scroll to top|share this|about the author|read next|view all|"
    r"\d+ comments?|post navigation)",
    re.I,
)
_TRAILER_MIN_FRACTION = 0.5
_LEADING_CHROME = {"skip to content", "menu", "home"}
_CATEGORY_LINE_RE = re.compile(r"^[A-Z][\w &-]{1,30}(, [A-Z][\w &-]{1,30}){0,4}$")


def _strip_generic_chrome(text: str, title: str, author: str) -> tuple[str, str]:
    """Drop leading page chrome (skip links, a `by <Name>` byline — returned
    as the author when none was given) and the trailing related-posts /
    comments / consent block. Returns (text, author)."""
    lines = text.splitlines()
    known = {t.strip().lower() for t in (title, author) if t and t.strip()}
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        if not ln or ln.lower() in _LEADING_CHROME or ln.lower() in known or _DATE_LINE_RE.match(ln):
            i += 1
        elif (m := _INLINE_BYLINE_RE.match(ln)) and _NAME_LINE_RE.match(m.group("name")):   # "by Brandon Roberts"
            if not author:
                author = m.group("name")
            i += 1
        elif _BYLINE_LINE_RE.match(ln):
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and _NAME_LINE_RE.match(lines[j].strip()):
                if not author:
                    author = lines[j].strip()
                i = j + 1
                k = i
                while k < len(lines) and not lines[k].strip():
                    k += 1
                if k < len(lines) and _CATEGORY_LINE_RE.match(lines[k].strip()):   # "Articles, Programming"
                    i = k + 1
            else:
                i += 1
        else:
            break
    lines = lines[i:]
    total = sum(len(ln) for ln in lines)
    seen = 0
    for k, ln in enumerate(lines):
        if seen >= _TRAILER_MIN_FRACTION * total and _TRAILER_RE.match(ln.strip()):
            lines = lines[:k]
            break
        seen += len(ln)
    return "\n".join(lines), author


def load_url_list(path: Path) -> list[tuple[str, str]]:
    """`[(url, author)]` from a curated list file.

    Format: `{"author": "<default author>", "urls": ["https://…", {"url": "…",
    "author": "…"}, …]}` — an entry may override the author. Order is kept;
    duplicates are dropped.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    default_author = data.get("author", "")
    out, seen = [], set()
    for entry in data["urls"]:
        url, author = (entry, default_author) if isinstance(entry, str) else (entry["url"], entry.get("author", default_author))
        if url not in seen:
            seen.add(url)
            out.append((url, author))
    return out


def fetch_generic_article(url: str, author: str = "", html: str | None = None) -> tuple[dict | None, bool]:
    """Fetch one article from an arbitrary (WordPress-style) site.

    Same (article, permanent_skip) contract as fetch_article. The body is the
    largest of the usual containers after navigation, widgets, share bars and
    comment blocks are removed; a page under GENERIC_MIN_WORDS (a video or
    podcast landing page) is a permanent skip.

    `html`, when given, is parsed instead of fetching `url` (which is then only
    the article's URL) — for callers that pre-process the page, e.g.
    `sources/url_lists/cissik_medvedyev.py` swapping table images for text.
    """
    if html is None:
        resp, permanent = _get_with_retry(url, timeout=30)
        if resp is None:
            return None, permanent
        html = resp.text
    soup = BeautifulSoup(html, "lxml")

    title = ""
    h1 = soup.find("h1")
    if h1 and len(h1.get_text(strip=True)) > 5:
        title = re.sub(r"\s+", " ", h1.get_text(" ", strip=True))
    elif soup.title:
        title = _SITE_TITLE_SUFFIX_RE.sub("", soup.title.get_text(strip=True))
    if not author:
        meta = soup.find("meta", attrs={"name": "author"})
        byline = soup.find(attrs={"rel": "author"}) or soup.find(class_=re.compile(r"author[-_]?name|byline", re.I))
        candidate = (meta.get("content") or "").strip() if meta else (byline.get_text(" ", strip=True) if byline else "")
        candidate = re.sub(r"^(written\s+)?by\s+", "", candidate, flags=re.I)
        if re.match(r"^[A-Z][a-zA-Z\s\-.']{3,40}$", candidate):
            author = candidate

    for tag in soup(["nav", "header", "footer", "script", "style", "aside", "form", "iframe", "noscript", "svg"]):
        tag.decompose()
    # Pick the container first, then clean inside it — a body-level theme class
    # like `content-sidebar` must not take the whole page with it.
    candidates = [el for sel in _GENERIC_CONTAINERS for el in soup.select(sel)] + ([soup.body] if soup.body else [])
    if not candidates:
        logger.warning(f"No content element found for {url}")
        return None, True
    sizes = {id(el): len(el.get_text(" ", strip=True)) for el in candidates}
    main = max(candidates, key=lambda el: sizes[id(el)])
    for el in candidates:                      # prefer the tightest container that still holds the article
        if el is not main and sizes[id(el)] >= 0.8 * sizes[id(main)]:
            main = el
    main_len = len(main.get_text(" ", strip=True))
    paragraphs = main.find_all("p")
    body_anchor = max(paragraphs, key=lambda p: len(p.get_text(" ", strip=True))) if paragraphs else None
    for tag in main.find_all(True, class_=_GENERIC_DROP) + main.find_all(True, id=_GENERIC_DROP):
        # a theme wrapper (`content-sidebar-wrap`) matches too — never drop the
        # element holding the article's longest paragraph
        if tag.parent is not None and (body_anchor is None or body_anchor not in tag.descendants):
            tag.decompose()
    for ul in main.find_all(["ul", "ol"]):      # link-only lists are menus / related posts
        items = ul.find_all("li", recursive=False)
        if items and all(
            li.find("a") is not None
            and len(li.get_text(" ", strip=True)) <= sum(len(a.get_text(" ", strip=True)) for a in li.find_all("a")) + 3
            for li in items
        ):
            ul.decompose()
    for teaser in main.find_all("article"):     # nested teaser cards (related-post grids)
        if len(teaser.get_text(" ", strip=True)) < 0.05 * main_len:
            teaser.decompose()
    if main.find("h1") is not None:
        main.find("h1").decompose()       # the title is stored on the source, not in the text

    text, author = _strip_generic_chrome(block_text(main), title, author)
    text = strip_article_header(text, title, author)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    words = len(text.split())
    if words < GENERIC_MIN_WORDS:
        logger.warning(f"Only {words} words at {url} (video/landing page?) — skipping")
        return None, True
    return {"title": title, "author": author or "Unknown", "text": text, "url": url}, False


# ── Charniga / Sportivny Press (Wayback Machine) ───────────────

def collect_charniga_urls() -> list[tuple[str, str]]:
    """Enumerate archived sportivnypress.com article URLs via the Wayback CDX API.

    sportivnypress.com is defunct, so every article is fetched from its Internet
    Archive snapshot. Returns (original_url, timestamp) pairs — one per unique
    article (deduplicated by the CDX urlkey, so http/https/www/trailing-slash
    variants collapse — ING-M1), keeping the most recent HTTP-200 text/html
    capture from before the domain lapsed to a parking service (ING-M3). The
    timestamp is carried through to build the raw snapshot URL at fetch time.
    """
    params = {
        "url": f"{CHARNIGA_DOMAIN}/*",
        "output": "json",
        "fl": "urlkey,original,timestamp",
        # The domain died Jan 2025; parking pages answer 200 text/html for
        # every path after that, so cap captures at 2024 (ING-M3).
        "to": "20241231",
        # Multiple `filter` values are ANDed by CDX.
        "filter": ["statuscode:200", "mimetype:text/html"],
    }
    # Through the shared retry helper — a single transient CDX 503 used to
    # return 0 URLs, and a real run then reported "Nothing to ingest",
    # masquerading as success (audit4-F1).
    resp, _ = _get_with_retry(WAYBACK_CDX_URL, timeout=60, params=params)
    if resp is None:
        logger.error("Wayback CDX query failed after retries")
        return []
    try:
        rows = resp.json()
    except ValueError as e:
        logger.error(f"Wayback CDX returned unparseable JSON: {e}")
        return []

    # rows[0] is the header (["urlkey","original","timestamp"]); the rest are
    # captures, potentially many per URL across crawls.
    if not rows or len(rows) < 2:
        logger.warning(f"Wayback CDX returned no captures for {CHARNIGA_DOMAIN}")
        return []

    header, *data = rows
    latest: dict[str, tuple[str, str]] = {}  # urlkey → (original, timestamp)
    for row in data:
        urlkey, original, timestamp = row[0], row[1], row[2]
        if _CHARNIGA_SKIP.search(original):
            continue
        if not _CHARNIGA_ARTICLE_RE.match(original):
            continue
        # Keep the most recent capture per urlkey — SURT form, so scheme/www/
        # port/slash variants of one essay share a key (ING-M1). Timestamps are
        # YYYYMMDDhhmmss, so a lexicographic max is chronological.
        if urlkey not in latest or timestamp > latest[urlkey][1]:
            latest[urlkey] = (original, timestamp)

    pairs = sorted(latest.values())
    logger.info(f"CDX: {len(data)} captures → {len(pairs)} unique article URLs")
    return pairs


# Transient statuses (rate limit / server-side) must not be conflated with
# permanent failures (404) when deciding whether to persist a URL as done.
# 403: usually WAF/rate-limiting for a bot UA, not a real denial — persisting
# it silently dropped the article forever (audit3-L3). 408: request timeout.
_RETRYABLE_HTTP_STATUSES = {403, 408, 429, 500, 502, 503, 504}


def _get_with_retry(url: str, timeout: int = 30, attempts: int = 3, params: dict | None = None):
    """GET with exponential backoff on transient failures (Wayback + live sites).

    Returns (response | None, permanent). permanent=True only for HTTP errors
    that will never succeed (404 etc.); rate limits, 5xx, timeouts and
    connection drops are retried and, if they persist, reported as transient so
    the caller keeps the URL pending for the next run (ING-H1, audit2-L1).
    """
    err = None
    for attempt in range(1, attempts + 1):
        try:
            resp = SESSION.get(url, timeout=timeout, params=params)
            resp.raise_for_status()
            return resp, False
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status not in _RETRYABLE_HTTP_STATUSES:
                logger.warning(f"Permanent HTTP {status} for {url}")
                return None, True
            err = e
        except requests.RequestException as e:
            err = e
        if attempt < attempts:
            wait = 2 ** attempt
            logger.warning(f"Fetch failed ({err}); retrying in {wait}s ({attempt}/{attempts})")
            time.sleep(wait)
    logger.warning(f"Transient failure persists for {url} — leaving pending for next run")
    return None, False


# The 41 URLs the 2026-09-16 run kept pending as "51-char snapshots" were
# Wayback captures of the site's bot-check interstitial ("One moment, please...
# Please wait while your request is being verified") — the archive had saved
# the challenge page, not the article (CHARNIGA-STUBS). Such a capture is
# recognisable by its text; the fix is to try the URL's earlier captures.
_BOT_CHECK_RE = re.compile(r"please wait while your request is being verified|one moment, please", re.I)
ALTERNATE_CAPTURES_TO_TRY = 6


def _is_bot_check(soup: BeautifulSoup) -> bool:
    return bool(_BOT_CHECK_RE.search(soup.get_text(" ", strip=True)[:400]))


def alternate_captures(original_url: str, before: str, limit: int = ALTERNATE_CAPTURES_TO_TRY) -> list[str]:
    """Earlier HTTP-200 text/html capture timestamps of one URL, newest first,
    strictly before `before`; [] on a CDX failure."""
    resp, _ = _get_with_retry(WAYBACK_CDX_URL, timeout=60, params={
        "url": original_url, "output": "json", "fl": "timestamp", "to": "20241231",
        "filter": ["statuscode:200", "mimetype:text/html"],
    })
    if resp is None:
        return []
    try:
        rows = resp.json()
    except ValueError:
        return []
    stamps = sorted({r[0] for r in rows[1:] if r and r[0] < before}, reverse=True)
    return stamps[:limit]


def fetch_charniga_snapshot(original_url: str, timestamp: str, *, try_alternates: bool = True) -> tuple[dict | None, bool]:
    """Fetch one archived Charniga article from the Wayback Machine and extract text.

    Returns (article, permanent_skip). When article is None, permanent_skip=True
    means the URL can never yield content (404, empty document, only bot-check
    captures) and may be persisted as processed; False means a transient failure
    or a suspiciously short extraction (Wayback hiccup, content-selector
    mismatch) — the URL must stay pending so a later run can retry it (ING-H1).
    A capture that is the site's bot-check page falls back to the URL's earlier
    captures (CHARNIGA-STUBS); if every tried capture is the challenge page the
    URL is reported permanent so re-runs stop retrying it.
    """
    snapshot = WAYBACK_RAW_FMT.format(timestamp=timestamp, original=original_url)
    resp, permanent = _get_with_retry(snapshot, timeout=30)
    if resp is None:
        return None, permanent
    if _is_bot_check(BeautifulSoup(resp.content, "lxml")):
        if not try_alternates:
            return None, True
        for alt in alternate_captures(original_url, timestamp):
            article, permanent = fetch_charniga_snapshot(original_url, alt, try_alternates=False)
            if article is not None:
                logger.info(f"Bot-check capture at {timestamp} for {original_url} — used capture {alt} instead")
                return article, permanent
        logger.warning(f"Every tried capture of {original_url} is the bot-check page — marking permanent")
        return None, True

    # Bytes, not resp.text — requests defaults charset-less text/* to
    # ISO-8859-1, mojibake-ing UTF-8 quotes/dashes in old captures; BS4's
    # detection honors the meta charset (ING-M4).
    soup = BeautifulSoup(resp.content, "lxml")

    # ── Title — WordPress h1.entry-title, else <title> (strip site suffix) ──
    title = ""
    h1 = soup.find(["h1", "h2"], class_=re.compile(r"entry[-_]title|post[-_]title", re.I))
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        title_tag = soup.find("title")
        if title_tag:
            # WP titles separate with hyphen, pipe, or en/em dash (&#8211; is
            # decoded to – by BS4) — strip all of them (ING-L1)
            title = re.sub(r"\s*[-|–—]\s*Sportivny Press.*$", "", title_tag.get_text(strip=True), flags=re.I)

    # ── Body — first matching content selector, then a generic fallback ──
    main = None
    for sel in CHARNIGA_CONTENT_SELECTORS:
        main = soup.find(**sel)
        if main:
            break
    if main is None:
        for tag in soup(["nav", "header", "footer", "script", "style", "aside"]):
            tag.decompose()
        main = soup.find("main") or soup.body

    if main is None:
        logger.warning(f"No content element found for {original_url}")
        return None, True

    # Strip share / related / comment / nav widgets inside the content container.
    for tag in main(["div", "section", "ul"], class_=re.compile(r"share|related|comment|nav|sidebar|meta", re.I)):
        tag.decompose()

    # block_text inserts \n\n paragraph markers so the chunker can split within
    # long articles (mutates `main` — must run after the title read above).
    text = strip_article_header(block_text(main), title)
    if len(text) < 200:
        # Could be a stub page, but could equally be a selector/theme mismatch
        # or a parking page — keep it pending rather than discarding forever.
        logger.warning(f"Very short article ({len(text)} chars) at {original_url} — skipping (kept pending)")
        return None, False

    # Author: Charniga was translator/publisher for the whole corpus. Individual
    # translations carry an original Russian byline (Roman, Medvedev, …); the
    # source record uses the curator here. TODO: parse a per-article byline
    # (.entry-meta / byline) to refine attribution if desired.
    return {"title": title or original_url, "author": "Andrew Charniga", "text": text, "url": original_url}, False


# ── Ingestion ──────────────────────────────────────────────────

def ingest_article(article: dict, pipeline_components: dict, run_stats: dict) -> tuple[dict, bool]:
    """Ingest a single article: classify, then route each section through the
    shared SectionProcessor (chunk → validate → embed, principle extraction).

    Returns (run_stats, success). success=False on a run-level failure so the
    caller can leave the URL out of the progress file and retry it next run
    (I-M4) — previously every attempt was marked ingested regardless.
    """
    sl: StructuredLoader = pipeline_components["structured_loader"]
    vl: VectorLoader = pipeline_components["vector_loader"]
    classifier: ContentClassifier = pipeline_components["classifier"]
    principle_extractor: PrincipleExtractor = pipeline_components["principle_extractor"]
    settings: Settings = pipeline_components["settings"]

    title = article["title"]
    author = article["author"]
    text = article["text"]
    url = article["url"]

    # Upsert source record (one per article)
    # url disambiguates same-titled pages under the constant curator author,
    # which UNIQUE(title, author) would otherwise merge into one source (ING-L1)
    source_id = sl.upsert_source(title=title, author=author, source_type="website", url=url)

    # Ingestion run tracking
    import hashlib
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    run_id = sl.create_run(
        source_id=source_id,
        file_path=url,
        file_hash=content_hash,
        config_snapshot={
            "embedding_model": settings.embedding_model,
            "llm_model": settings.llm_model,
        },
    )

    try:
        word_count = len(text.split())
        target = SectionTarget(
            source_id=source_id, title=title, author=author,
            chunker=SemanticChunker.for_web_article(word_count), run_id=run_id,
        )
        # Same per-section routing as pipeline.py (I-L11), with no structured
        # handler: web articles have no table/program/exercise loaders, so those
        # sections are chunked as prose rather than dropped (I-M2).
        contextualize = bool(pipeline_components.get("contextualize"))
        processor = SectionProcessor(
            settings, vl, sl, principle_extractor,
            contextualize=contextualize,
            context_model=light_model_for(settings, pipeline_components.get("context_model")) if contextualize else None,
        )
        stats = new_section_stats()

        for section in classifier.classify_sections(text, title):
            try:
                processor.process(section, target, stats)
            except Exception as e:
                logger.error(f"Section error in '{title}': {e}")
                processor.rollback()

        # PRIN-AUDIT per article, against the article text itself
        if pipeline_components.get("principle_audit") and stats["principles"]:
            from principle_audit import run_audit_pass
            stats["principle_audit"] = run_audit_pass(source_id, settings, text)

        # Jev junk pass per article, as pipeline.py does per source (JEV-1a)
        if pipeline_components.get("quarantine") and stats["chunks_loaded"]:
            stats["chunks_quarantined_jev"] = run_quarantine_pass(source_id, settings)

        sl.complete_run(run_id, stats)
        chunks_loaded = stats["chunks_loaded"]
        principles_count = stats["principles"]

        run_stats["articles_ingested"] += 1
        run_stats["chunks_total"] += chunks_loaded
        run_stats["principles_total"] += principles_count
        logger.info(
            f"  Ingested: '{title}' — {chunks_loaded} chunks, {principles_count} principles"
        )

    except Exception as e:
        import traceback
        sl.fail_run(run_id, error_message=str(e),
                    error_details={"traceback": traceback.format_exc()})
        logger.error(f"Failed to ingest '{title}': {e}")
        return run_stats, False

    return run_stats, True


# ── Progress tracking ──────────────────────────────────────────

def load_progress(path: Path = PROGRESS_FILE) -> set[str]:
    if path.exists():
        return set(json.loads(path.read_text()))
    return set()


def save_progress(ingested_urls: set[str], path: Path = PROGRESS_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(ingested_urls), indent=2))


# ── Main ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Ingest web articles (Catalyst Athletics live, or Charniga via Wayback Machine)")
    parser.add_argument(
        "--site", choices=["catalyst", "charniga", "urls"], default="catalyst",
        help="Source: 'catalyst' (live crawl), 'charniga' (Wayback Machine; sportivnypress.com is defunct) "
             "or 'urls' (curated list, --url-file)",
    )
    parser.add_argument("--url-file", type=Path, default=None,
                        help="--site urls: JSON list file (sources/url_lists/<name>.json)")
    parser.add_argument(
        "--categories", nargs="+",
        choices=list(CATALYST_CATEGORIES.keys()),
        default=list(CATALYST_CATEGORIES.keys()),
        help="Catalyst categories to ingest (default: all; ignored for --site charniga)",
    )
    parser.add_argument("--limit", type=int, default=0,
                        help="Max articles to ingest (0 = no limit, useful for testing)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Collect URLs only, print count, don't ingest")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Seconds between article requests (default: 1.0)")
    parser.add_argument("--contextualize", action="store_true",
                        help="Write an LLM retrieval-context prefix into each chunk before embedding (RAG-M3)")
    parser.add_argument("--context-model", default=None,
                        help="Model for --contextualize (default: settings.light_model)")
    parser.add_argument("--no-principle-audit", action="store_true",
                        help="Keep principle numbers the article never states (skip the PRIN-AUDIT pass)")
    parser.add_argument("--no-quarantine", action="store_true",
                        help="Skip the per-article Jev junk pass (needs TYPESAFE_API_KEY; skipped with a warning without it)")
    args = parser.parse_args()

    # ── Collect URLs (per-site) ──
    # all_urls: list of (url, meta) — meta is the Catalyst category name, or the
    # Wayback snapshot timestamp for Charniga (needed to build the snapshot URL).
    if args.site == "charniga":
        progress_file = CHARNIGA_PROGRESS_FILE
        logger.info("Collecting archived sportivnypress.com URLs from the Wayback Machine")
        all_urls: list[tuple[str, str]] = collect_charniga_urls()
    elif args.site == "urls":
        if args.url_file is None:
            parser.error("--site urls needs --url-file")
        progress_file = URL_LIST_PROGRESS_FILE
        all_urls = load_url_list(args.url_file)      # meta = author
        logger.info(f"Loaded {len(all_urls)} URLs from {args.url_file}")
    else:
        progress_file = PROGRESS_FILE
        all_urls = []  # (url, category_name)
        for key in args.categories:
            cat = CATALYST_CATEGORIES[key]
            logger.info(f"Collecting URLs for: {cat['name']}")
            urls = collect_category_urls(cat["section"], cat["name"])
            logger.info(f"  Found {len(urls)} articles in {cat['name']}")
            all_urls.extend((url, cat["name"]) for url in urls)

    logger.info(f"Total URLs collected: {len(all_urls)}")

    if not all_urls and not args.dry_run:
        # 0 collected URLs used to fall through to "Nothing to ingest. All URLs
        # already processed." — a transient CDX/site failure masquerading as a
        # completed run (audit4-F1). Fail loudly instead.
        logger.error(
            "URL collection returned nothing — likely a transient enumeration "
            "failure, not an empty corpus. Nothing was marked ingested; re-run later."
        )
        raise SystemExit(1)

    if args.dry_run:
        for url, meta in all_urls:
            print(f"[{meta}] {url}")
        print(f"\nTotal: {len(all_urls)} articles")
        return

    # ── Filter already-ingested ──
    ingested_urls = load_progress(progress_file)
    pending = [(url, meta) for url, meta in all_urls if url not in ingested_urls]
    logger.info(f"Pending (not yet ingested): {len(pending)} / {len(all_urls)}")

    if args.limit:
        pending = pending[:args.limit]
        logger.info(f"Capped to {len(pending)} articles (--limit {args.limit})")

    if not pending:
        logger.info("Nothing to ingest. All URLs already processed.")
        return

    # ── Set up pipeline components ──
    settings = Settings()
    settings.ensure_working_dirs()
    components = {
        "settings": settings,
        "structured_loader": StructuredLoader(settings),
        "vector_loader": VectorLoader(settings),
        "classifier": ContentClassifier(settings),
        "principle_extractor": PrincipleExtractor(settings),
        "contextualize": args.contextualize,
        "context_model": light_model_for(settings, args.context_model),
        "quarantine": not args.no_quarantine,
        "principle_audit": not args.no_principle_audit,
    }

    run_stats = {"articles_ingested": 0, "chunks_total": 0, "principles_total": 0}

    # ── Ingest loop ──
    successes = 0
    progress = Progress(len(pending), logger, label="article")
    for i, (url, meta) in enumerate(pending, 1):
        progress.tick(i, f"{meta}: {url} · ingested={run_stats['articles_ingested']} chunks={run_stats['chunks_total']}")

        # One bad article must not abort the whole run: parts of the ingest
        # (upsert_source, run creation) sit outside ingest_article's internal
        # try, and an escaped exception used to kill the loop with the DB
        # connection in a failed-transaction state (audit3-M1). The URL stays
        # pending for the next run.
        try:
            if args.site == "charniga":
                article, permanent_skip = fetch_charniga_snapshot(url, meta)  # meta = Wayback timestamp
            elif args.site == "urls":
                if "catalystathletics.com" in url:                             # Catalyst has its own selectors
                    article, permanent_skip = fetch_article(url)
                    if article is not None and meta:
                        article["author"] = meta
                else:
                    article, permanent_skip = fetch_generic_article(url, meta)  # meta = author
            else:
                article, permanent_skip = fetch_article(url)

            if article is None:
                if permanent_skip:
                    ingested_urls.add(url)  # can never succeed — safe to mark as seen
                    save_progress(ingested_urls, progress_file)
                # transient failures stay OUT of the progress file so the next run
                # retries them instead of silently discarding corpus (ING-H1)
                time.sleep(args.delay)
                continue

            run_stats, ok = ingest_article(article, components, run_stats)
        except Exception as e:
            logger.error(f"Unhandled error ingesting {url}: {e} — URL stays pending")
            rollback_loaders(components["structured_loader"], components["vector_loader"])
            time.sleep(args.delay)
            continue

        if ok:
            ingested_urls.add(url)  # only mark successful ingests — failures retry next run
            successes += 1
            # Flush every 10 SUCCESSES — keyed on the loop index, a crash could
            # lose up to 9 successful (paid) ingests from the file (ING-L2)
            if successes % 10 == 0:
                save_progress(ingested_urls, progress_file)

        time.sleep(args.delay)

    save_progress(ingested_urls, progress_file)

    # ── Summary ──
    print("\nWeb ingestion complete:")
    print(f"  Articles ingested: {run_stats['articles_ingested']}")
    print(f"  Chunks loaded:     {run_stats['chunks_total']}")
    print(f"  Principles:        {run_stats['principles_total']}")


if __name__ == "__main__":
    main()
