# tests/test_ingest_web.py
"""
No-key unit tests for ingest_web.ingest_article's success-flag contract (I-M4):
a run-level failure must return success=False so the caller can leave the URL
out of the progress file and retry it next run.

Run: python tests/test_ingest_web.py
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from ingest_web import ingest_article

RESULTS = []


def _test(name, fn):
    try:
        fn()
        RESULTS.append(("PASS", name))
    except AssertionError as e:
        RESULTS.append(("FAIL", name, str(e)))
    except Exception as e:
        RESULTS.append(("ERROR", name, f"{type(e).__name__}: {e}"))


_ARTICLE = {"title": "T", "author": "A", "text": "some article text", "url": "http://x/article/1/"}


def _components(classify_raises=False):
    sl = MagicMock()
    sl.upsert_source.return_value = 1
    sl.create_run.return_value = 7
    vl = MagicMock()
    vl.load_chunks.return_value = 1
    vl.last_skipped_count = 0
    classifier = MagicMock()
    if classify_raises:
        classifier.classify_sections.side_effect = RuntimeError("boom")
    else:
        classifier.classify_sections.return_value = []  # no sections → clean run
    settings = MagicMock()
    settings.embedding_model = "m"
    settings.llm_model = "l"
    return {
        "structured_loader": sl,
        "vector_loader": vl,
        "classifier": classifier,
        "principle_extractor": MagicMock(),
        "settings": settings,
    }


def _stats():
    return {"articles_ingested": 0, "chunks_total": 0, "principles_total": 0}


def test_ingest_article_success_returns_true():
    comps = _components()
    _, ok = ingest_article(_ARTICLE, comps, _stats())
    assert ok is True
    comps["structured_loader"].complete_run.assert_called_once()


def test_ingest_article_failure_returns_false():
    comps = _components(classify_raises=True)
    _, ok = ingest_article(_ARTICLE, comps, _stats())
    assert ok is False
    comps["structured_loader"].fail_run.assert_called_once()


def test_table_section_chunked_not_dropped():
    """I-M2: a TABLE section in a web article is chunked as prose (there's no
    structured loader in the web path) instead of being silently dropped."""
    from processors.classifier import ClassifiedSection, ContentType
    comps = _components()
    comps["classifier"].classify_sections.return_value = [
        ClassifiedSection(
            content="Week 1: Snatch 70% x 3, Clean 75% x 2. Table content here.",
            content_type=ContentType.TABLE,
            metadata={},
        )
    ]
    _, ok = ingest_article(_ARTICLE, comps, _stats())
    assert ok is True
    comps["vector_loader"].load_chunks.assert_called()  # not dropped


# ── ING-H1: transient Wayback failures must not be marked ingested ────────────

def _mk_resp(status=200, text="", raise_http=False):
    import requests
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.content = text.encode("utf-8")
    if raise_http:
        resp.raise_for_status.side_effect = requests.HTTPError(response=resp)
    else:
        resp.raise_for_status.return_value = None
    return resp


def test_charniga_connection_error_is_transient_and_retried():
    import ingest_web
    import requests
    from ingest_web import fetch_charniga_snapshot

    with patch.object(ingest_web.SESSION, "get", side_effect=requests.ConnectionError("boom")) as mock_get, \
         patch("time.sleep"):
        article, permanent = fetch_charniga_snapshot("http://sportivnypress.com/2016/x/", "20200101000000")
    assert article is None
    assert permanent is False, "connection errors are transient — URL must stay pending"
    assert mock_get.call_count == 3, f"expected 3 attempts, got {mock_get.call_count}"


def test_charniga_404_is_permanent_no_retry():
    import ingest_web
    from ingest_web import fetch_charniga_snapshot

    with patch.object(ingest_web.SESSION, "get", return_value=_mk_resp(status=404, raise_http=True)) as mock_get, \
         patch("time.sleep"):
        article, permanent = fetch_charniga_snapshot("http://sportivnypress.com/2016/x/", "20200101000000")
    assert article is None
    assert permanent is True, "a 404 capture can never succeed — safe to persist"
    assert mock_get.call_count == 1, "no retry for permanent failures"


def test_charniga_429_retries_then_transient():
    import ingest_web
    from ingest_web import fetch_charniga_snapshot

    with patch.object(ingest_web.SESSION, "get", return_value=_mk_resp(status=429, raise_http=True)) as mock_get, \
         patch("time.sleep"):
        article, permanent = fetch_charniga_snapshot("http://sportivnypress.com/2016/x/", "20200101000000")
    assert article is None and permanent is False
    assert mock_get.call_count == 3


def test_charniga_short_content_is_transient():
    """<200 chars usually means the content selector missed (theme mismatch) or
    a parking page — must NOT be permanently marked ingested."""
    import ingest_web
    from ingest_web import fetch_charniga_snapshot

    html = "<html><body><div class='entry-content'><p>too short</p></div></body></html>"
    with patch.object(ingest_web.SESSION, "get", return_value=_mk_resp(text=html)):
        article, permanent = fetch_charniga_snapshot("http://sportivnypress.com/2016/x/", "20200101000000")
    assert article is None and permanent is False


def test_charniga_good_article_parses():
    import ingest_web
    from ingest_web import fetch_charniga_snapshot

    body = "<p>" + "Soviet weightlifting methodology. " * 20 + "</p>"
    html = (
        "<html><head><title>Essay – Sportivny Press</title></head><body>"
        "<h1 class='entry-title'>Essay</h1>"
        f"<div class='entry-content'>{body}</div></body></html>"
    )
    with patch.object(ingest_web.SESSION, "get", return_value=_mk_resp(text=html)):
        article, permanent = fetch_charniga_snapshot("http://sportivnypress.com/2016/x/", "20200101000000")
    assert article is not None
    assert article["title"] == "Essay"
    assert len(article["text"]) >= 200


# ── ING-M1/M2/M3: CDX enumeration — urlkey dedup, article shape, pre-2025 cap ─

def test_cdx_dedupes_variants_and_caps_pre2025():
    import ingest_web
    from ingest_web import collect_charniga_urls

    rows = [
        ["urlkey", "original", "timestamp"],
        ["com,sportivnypress)/2016/essay", "http://sportivnypress.com/2016/essay/", "20200101000000"],
        ["com,sportivnypress)/2016/essay", "https://www.sportivnypress.com/2016/essay/", "20210101000000"],
        ["com,sportivnypress)/2016/other", "https://sportivnypress.com/2016/other/", "20200601000000"],
    ]
    resp = MagicMock()
    resp.json.return_value = rows
    resp.raise_for_status.return_value = None
    with patch.object(ingest_web.SESSION, "get", return_value=resp) as mock_get:
        pairs = collect_charniga_urls()
    assert len(pairs) == 2, f"scheme/www variants must collapse via urlkey (ING-M1): {pairs}"
    assert ("https://www.sportivnypress.com/2016/essay/", "20210101000000") in pairs, pairs
    params = mock_get.call_args.kwargs["params"]
    assert "urlkey" in params["fl"], "dedup must key on the CDX urlkey (SURT)"
    assert params.get("to") == "20241231", "cap captures before the 2025 domain lapse (ING-M3)"


def test_cdx_requires_article_shaped_urls():
    import ingest_web
    from ingest_web import collect_charniga_urls

    def row(orig, ts="20200101000000"):
        return ["key_" + orig, orig, ts]

    rows = [
        ["urlkey", "original", "timestamp"],
        row("https://sportivnypress.com/"),                            # homepage
        row("https://sportivnypress.com/2016/"),                       # bare date archive
        row("https://sportivnypress.com/about/"),                      # static WP page
        row("https://sportivnypress.com/2016/essay/comment-page-2/"),  # comment pagination
        row("https://sportivnypress.com/2016/essay/"),                 # real article
    ]
    resp = MagicMock()
    resp.json.return_value = rows
    resp.raise_for_status.return_value = None
    with patch.object(ingest_web.SESSION, "get", return_value=resp):
        pairs = collect_charniga_urls()
    assert pairs == [("https://sportivnypress.com/2016/essay/", "20200101000000")], \
        f"only /YYYY/slug/ article URLs should survive (ING-M2): {pairs}"


# ── ING-M4: captures without a charset header must not mojibake ───────────────

def test_charniga_utf8_without_charset_header_no_mojibake():
    import ingest_web
    from ingest_web import fetch_charniga_snapshot

    body = "<p>" + "Restoration — the Soviet method. " * 15 + "</p>"
    html = (
        '<html><head><meta charset="utf-8"><title>E</title></head><body>'
        f"<div class='entry-content'>{body}</div></body></html>"
    )
    resp = MagicMock()
    resp.status_code = 200
    resp.content = html.encode("utf-8")
    # requests defaults text/* without charset to ISO-8859-1 → mojibake in .text
    resp.text = html.encode("utf-8").decode("iso-8859-1")
    resp.raise_for_status.return_value = None
    with patch.object(ingest_web.SESSION, "get", return_value=resp):
        article, _ = fetch_charniga_snapshot("http://sportivnypress.com/2016/x/", "20200101000000")
    assert article is not None
    assert "—" in article["text"], "em-dash lost"
    assert "â€”" not in article["text"], "mojibake reached the chunker (ING-M4)"


# ── audit2 M1/M2: article-shape regex edge cases ──────────────────────────────

def test_article_regex_rejects_month_archives():
    """audit2-M1: WP month archives (/YYYY/MM/) are listing pages, not articles."""
    from ingest_web import _CHARNIGA_ARTICLE_RE
    assert not _CHARNIGA_ARTICLE_RE.match("http://sportivnypress.com/2016/05/")
    assert not _CHARNIGA_ARTICLE_RE.match("https://www.sportivnypress.com/2016/05")
    assert not _CHARNIGA_ARTICLE_RE.match("http://sportivnypress.com/2016/")
    # real permalinks still pass
    assert _CHARNIGA_ARTICLE_RE.match("http://sportivnypress.com/2016/russian-training/")
    assert _CHARNIGA_ARTICLE_RE.match("https://www.sportivnypress.com/2016/05/essay-name/")


def test_article_regex_accepts_port_qualified_originals():
    """audit2-M2: CDX originals from HTTP-era crawls carry :80 — the filter runs
    BEFORE urlkey dedup, so rejecting them silently drops whole essays."""
    from ingest_web import _CHARNIGA_ARTICLE_RE
    assert _CHARNIGA_ARTICLE_RE.match("http://sportivnypress.com:80/2016/russian-training/")
    assert _CHARNIGA_ARTICLE_RE.match("https://www.sportivnypress.com:443/2016/essay/")


# ── audit2-L1: Catalyst transient failures must stay pending ─────────────────

def test_catalyst_connection_error_is_transient_and_retried():
    import ingest_web
    import requests
    from ingest_web import fetch_article

    with patch.object(ingest_web.SESSION, "get", side_effect=requests.ConnectionError("boom")) as mock_get, \
         patch("time.sleep"):
        article, permanent = fetch_article("http://x/article/1/")
    assert article is None
    assert permanent is False, "a Wi-Fi blip must not permanently drop the article (audit2-L1)"
    assert mock_get.call_count == 3


def test_catalyst_404_is_permanent():
    import ingest_web
    from ingest_web import fetch_article

    with patch.object(ingest_web.SESSION, "get", return_value=_mk_resp(status=404, raise_http=True)), \
         patch("time.sleep"):
        article, permanent = fetch_article("http://x/article/1/")
    assert article is None and permanent is True


# ── ING-L1: title suffix stripping + sources.url population ──────────────────

def test_charniga_title_strips_endash_suffix():
    import ingest_web
    from ingest_web import fetch_charniga_snapshot

    body = "<p>" + "Text and more text. " * 30 + "</p>"
    html = (
        "<html><head><title>Essay Name – Sportivny Press Weightlifting Library</title></head>"
        f"<body><div class='entry-content'>{body}</div></body></html>"
    )
    with patch.object(ingest_web.SESSION, "get", return_value=_mk_resp(text=html)):
        article, _ = fetch_charniga_snapshot("http://sportivnypress.com/2016/x/", "20200101000000")
    assert article is not None
    assert article["title"] == "Essay Name", article["title"]


def test_ingest_article_passes_url_to_source():
    comps = _components()
    ingest_article(_ARTICLE, comps, _stats())
    kwargs = comps["structured_loader"].upsert_source.call_args.kwargs
    assert kwargs.get("url") == _ARTICLE["url"], \
        "sources.url must disambiguate same-titled pages (ING-L1)"


# ── ING-L2: progress flushed on success count, not loop index ────────────────

def test_progress_flush_counts_successes():
    import inspect

    import ingest_web
    src = inspect.getsource(ingest_web.main)
    assert "successes % 10" in src, \
        "a crash must lose at most 9 SUCCESSFUL ingests, not 9 pending items (ING-L2)"


# ── ING-M5: parse-prompt goal vocabulary must match the DB CHECK ──────────────

def test_program_parse_prompt_goal_line_matches_db_check():
    from pipeline import _PROGRAM_PARSE_PROMPT
    goal_line = next(line for line in _PROGRAM_PARSE_PROMPT.splitlines() if '"goal"' in line)
    assert "technique_focus" in goal_line and "peaking" in goal_line, goal_line
    assert "accumulation" not in goal_line and "intensification" not in goal_line, \
        f"legacy goal labels violate the program_templates CHECK: {goal_line}"


if __name__ == "__main__":
    for name, fn in [(n, f) for n, f in globals().items() if n.startswith("test_")]:
        _test(name, fn)

    passed = sum(1 for r in RESULTS if r[0] == "PASS")
    failed = sum(1 for r in RESULTS if r[0] in ("FAIL", "ERROR"))
    for r in RESULTS:
        detail = f"  → {r[2]}" if len(r) > 2 else ""
        print(f"  {r[0]}  {r[1]}{detail}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
