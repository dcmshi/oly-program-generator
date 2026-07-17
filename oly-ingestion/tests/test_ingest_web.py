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
