# ingest_web.py
"""
Ingest web articles into the pipeline.

Two sources are supported via --site:
  * catalyst (default) — Catalyst Athletics; crawls live category pages.
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
from processors.classifier import ContentClassifier, ContentType
from processors.principle_extractor import PrincipleExtractor

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
WAYBACK_CDX_URL = "http://web.archive.org/cdx/search/cdx"
# The `id_` suffix returns the RAW capture (no Wayback toolbar / JS injection),
# so the parsed HTML is byte-identical to what was originally served.
WAYBACK_RAW_FMT = "http://web.archive.org/web/{timestamp}id_/{original}"

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
# (?!\d{1,2}/?$) — a purely numeric final segment is a month archive (/2016/05/),
# which the optional month group let backtrack through as a "slug" (audit2-M1).
_CHARNIGA_ARTICLE_RE = re.compile(
    r"^https?://(www\.)?sportivnypress\.com(:\d+)?/\d{4}(/\d{2})?/(?!\d{1,2}/?$)[^/]+/?$",
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
    text = block_text(main)

    if len(text) < 200:
        logger.warning(f"Very short article ({len(text)} chars) at {url} — skipping")
        return None, True

    return {"title": title, "author": author, "text": text, "url": url}, False


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
    try:
        resp = SESSION.get(WAYBACK_CDX_URL, params=params, timeout=60)
        resp.raise_for_status()
        rows = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.error(f"Wayback CDX query failed: {e}")
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


def _get_with_retry(url: str, timeout: int = 30, attempts: int = 3):
    """GET with exponential backoff on transient failures (Wayback + live sites).

    Returns (response | None, permanent). permanent=True only for HTTP errors
    that will never succeed (404 etc.); rate limits, 5xx, timeouts and
    connection drops are retried and, if they persist, reported as transient so
    the caller keeps the URL pending for the next run (ING-H1, audit2-L1).
    """
    err = None
    for attempt in range(1, attempts + 1):
        try:
            resp = SESSION.get(url, timeout=timeout)
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


def fetch_charniga_snapshot(original_url: str, timestamp: str) -> tuple[dict | None, bool]:
    """Fetch one archived Charniga article from the Wayback Machine and extract text.

    Returns (article, permanent_skip). When article is None, permanent_skip=True
    means the URL can never yield content (404, empty document) and may be
    persisted as processed; False means a transient failure or a suspiciously
    short extraction (Wayback hiccup, content-selector mismatch) — the URL must
    stay pending so a later run can retry it (ING-H1).
    """
    snapshot = WAYBACK_RAW_FMT.format(timestamp=timestamp, original=original_url)
    resp, permanent = _get_with_retry(snapshot, timeout=30)
    if resp is None:
        return None, permanent

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
    text = block_text(main)
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
    """Ingest a single article through chunker → classifier → vector store.

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
        chunker = SemanticChunker.for_web_article(word_count)

        sections = classifier.classify_sections(text, title)
        chunks_loaded = 0
        chunks_skipped = 0
        principles_count = 0

        for section in sections:
            try:
                # Web articles have no structured table/program/exercise loaders,
                # so anything that isn't a pure PRINCIPLE section is chunked as
                # prose — otherwise TABLE/PROGRAM/EXERCISE sections were dropped
                # silently, losing their text (I-M2).
                if section.content_type != ContentType.PRINCIPLE:
                    from pipeline import IngestionPipeline
                    chunk_type = IngestionPipeline._infer_chunk_type(section)
                    chunks = chunker.chunk(
                        text=section.content,
                        metadata={"chapter": "", "chunk_type": chunk_type},
                        source_title=title,
                        author=author,
                    )
                    loaded = vl.load_chunks(
                        chunks, source_id,
                        run_id=run_id,
                        structured_loader=sl,
                    )
                    chunks_loaded += loaded
                    chunks_skipped += vl.last_skipped_count

                if section.content_type in (ContentType.PRINCIPLE, ContentType.MIXED):
                    principles = principle_extractor.extract(
                        text=section.content,
                        source_title=title,
                        source_id=source_id,
                    )
                    sl.load_principles(principles, source_id)
                    principles_count += len(principles)

            except Exception as e:
                logger.error(f"Section error in '{title}': {e}")
                try:
                    vl.conn.rollback()
                    sl.conn.rollback()
                except Exception as rb_err:
                    logger.debug(f"Rollback failed (non-fatal): {rb_err}")

        sl.complete_run(run_id, {
            "chunks_loaded": chunks_loaded,
            "prose_chunks": chunks_loaded + chunks_skipped,
            "prose_chunks_valid": chunks_loaded + chunks_skipped,
            "chunks_skipped_dedup": chunks_skipped,
            "principles": principles_count,
        })

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
        "--site", choices=["catalyst", "charniga"], default="catalyst",
        help="Source: 'catalyst' (live crawl) or 'charniga' (Wayback Machine; sportivnypress.com is defunct)",
    )
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
    args = parser.parse_args()

    # ── Collect URLs (per-site) ──
    # all_urls: list of (url, meta) — meta is the Catalyst category name, or the
    # Wayback snapshot timestamp for Charniga (needed to build the snapshot URL).
    if args.site == "charniga":
        progress_file = CHARNIGA_PROGRESS_FILE
        logger.info("Collecting archived sportivnypress.com URLs from the Wayback Machine")
        all_urls: list[tuple[str, str]] = collect_charniga_urls()
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
    }

    run_stats = {"articles_ingested": 0, "chunks_total": 0, "principles_total": 0}

    # ── Ingest loop ──
    successes = 0
    for i, (url, meta) in enumerate(pending, 1):
        logger.info(f"[{i}/{len(pending)}] {meta}: {url}")

        # One bad article must not abort the whole run: parts of the ingest
        # (upsert_source, run creation) sit outside ingest_article's internal
        # try, and an escaped exception used to kill the loop with the DB
        # connection in a failed-transaction state (audit3-M1). The URL stays
        # pending for the next run.
        try:
            if args.site == "charniga":
                article, permanent_skip = fetch_charniga_snapshot(url, meta)  # meta = Wayback timestamp
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
            for comp_key in ("structured_loader", "vector_loader"):
                try:
                    components[comp_key].conn.rollback()
                except Exception:
                    pass
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
