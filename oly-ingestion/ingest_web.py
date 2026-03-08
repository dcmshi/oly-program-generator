# ingest_web.py
"""
Ingest web articles from Catalyst Athletics into the pipeline.

Crawls category pages to collect article URLs, fetches each article,
extracts clean text, and ingests through the same chunker/classifier/
vector_loader stack as PDF/EPUB sources. Each article gets its own
source record (type='website').

Progress is tracked in sources/catalyst_progress.json — re-running
skips already-ingested URLs. Chunk-level dedup (content_hash) also
prevents re-embedding identical content.

Usage:
    python ingest_web.py                        # all priority categories
    python ingest_web.py --categories technique program_design
    python ingest_web.py --limit 20             # cap for testing
    python ingest_web.py --dry-run              # collect URLs, no ingestion
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
from processors.chunker import SemanticChunker
from processors.classifier import ContentClassifier, ContentType
from processors.principle_extractor import PrincipleExtractor
from loaders.vector_loader import VectorLoader
from loaders.structured_loader import StructuredLoader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BASE_URL = "https://www.catalystathletics.com"
PROGRESS_FILE = Path(__file__).parent / "sources" / "catalyst_progress.json"

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


# ── URL collection ─────────────────────────────────────────────

def collect_category_urls(section_id: int, section_name: str) -> list[str]:
    """Paginate through a category page and return all article URLs."""
    urls = []
    start = 0
    page_size = 10

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

        # Check if there's a next page
        has_next = any(
            f"start={start + page_size}" in (a.get("href") or "")
            for a in soup.find_all("a", href=True)
        )
        if not has_next:
            break

        start += page_size
        time.sleep(0.5)

    return list(dict.fromkeys(urls))  # global dedupe


# ── Article fetching & extraction ──────────────────────────────

def fetch_article(url: str) -> dict | None:
    """Fetch an article URL and return title, author, and body text."""
    try:
        resp = SESSION.get(url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return None

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
        lines = [l.strip() for l in main.get_text(separator="\n", strip=True).split("\n") if l.strip()]
        # Author line is typically the second line (after title), before the date
        if len(lines) > 1:
            candidate = lines[1]
            # Plausible author: 2-4 words, no digits, not a date
            if re.match(r"^[A-Z][a-zA-Z\s\-\.]{3,40}$", candidate) and not re.search(r"\d", candidate):
                author = candidate

    if main is None:
        logger.warning(f"No content element found for {url}")
        return None

    text = main.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)

    if len(text) < 200:
        logger.warning(f"Very short article ({len(text)} chars) at {url} — skipping")
        return None

    return {"title": title, "author": author, "text": text, "url": url}


# ── Ingestion ──────────────────────────────────────────────────

def ingest_article(article: dict, pipeline_components: dict, run_stats: dict) -> dict:
    """Ingest a single article through chunker → classifier → vector store."""
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
    source_id = sl.upsert_source(title=title, author=author, source_type="website")

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
        principles_count = 0

        for section in sections:
            try:
                if section.content_type in (ContentType.PROSE, ContentType.MIXED):
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
                except Exception:
                    pass

        sl.complete_run(run_id, {
            "chunks_loaded": chunks_loaded,
            "prose_chunks": chunks_loaded,
            "prose_chunks_valid": chunks_loaded,
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

    return run_stats


# ── Progress tracking ──────────────────────────────────────────

def load_progress() -> set[str]:
    if PROGRESS_FILE.exists():
        return set(json.loads(PROGRESS_FILE.read_text()))
    return set()


def save_progress(ingested_urls: set[str]):
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps(sorted(ingested_urls), indent=2))


# ── Main ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Ingest Catalyst Athletics web articles")
    parser.add_argument(
        "--categories", nargs="+",
        choices=list(CATALYST_CATEGORIES.keys()),
        default=list(CATALYST_CATEGORIES.keys()),
        help="Categories to ingest (default: all priority categories)",
    )
    parser.add_argument("--limit", type=int, default=0,
                        help="Max articles to ingest (0 = no limit, useful for testing)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Collect URLs only, print count, don't ingest")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Seconds between article requests (default: 1.0)")
    args = parser.parse_args()

    # ── Collect URLs ──
    all_urls: list[tuple[str, str]] = []  # (url, category_name)
    for key in args.categories:
        cat = CATALYST_CATEGORIES[key]
        logger.info(f"Collecting URLs for: {cat['name']}")
        urls = collect_category_urls(cat["section"], cat["name"])
        logger.info(f"  Found {len(urls)} articles in {cat['name']}")
        all_urls.extend((url, cat["name"]) for url in urls)

    logger.info(f"Total URLs collected: {len(all_urls)}")

    if args.dry_run:
        for url, cat in all_urls:
            print(f"[{cat}] {url}")
        print(f"\nTotal: {len(all_urls)} articles")
        return

    # ── Filter already-ingested ──
    ingested_urls = load_progress()
    pending = [(url, cat) for url, cat in all_urls if url not in ingested_urls]
    logger.info(f"Pending (not yet ingested): {len(pending)} / {len(all_urls)}")

    if args.limit:
        pending = pending[:args.limit]
        logger.info(f"Capped to {len(pending)} articles (--limit {args.limit})")

    if not pending:
        logger.info("Nothing to ingest. All URLs already processed.")
        return

    # ── Set up pipeline components ──
    settings = Settings()
    components = {
        "settings": settings,
        "structured_loader": StructuredLoader(settings),
        "vector_loader": VectorLoader(settings),
        "classifier": ContentClassifier(settings),
        "principle_extractor": PrincipleExtractor(settings),
    }

    run_stats = {"articles_ingested": 0, "chunks_total": 0, "principles_total": 0}

    # ── Ingest loop ──
    for i, (url, category) in enumerate(pending, 1):
        logger.info(f"[{i}/{len(pending)}] {category}: {url}")

        article = fetch_article(url)
        if article is None:
            ingested_urls.add(url)  # mark as seen to avoid retrying bad URLs
            save_progress(ingested_urls)
            time.sleep(args.delay)
            continue

        run_stats = ingest_article(article, components, run_stats)
        ingested_urls.add(url)

        # Save progress every 10 articles
        if i % 10 == 0:
            save_progress(ingested_urls)

        time.sleep(args.delay)

    save_progress(ingested_urls)

    # ── Summary ──
    print(f"\nWeb ingestion complete:")
    print(f"  Articles ingested: {run_stats['articles_ingested']}")
    print(f"  Chunks loaded:     {run_stats['chunks_total']}")
    print(f"  Principles:        {run_stats['principles_total']}")


if __name__ == "__main__":
    main()
