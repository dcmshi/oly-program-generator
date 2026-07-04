# tests/test_ingest_web.py
"""
No-key unit tests for ingest_web.ingest_article's success-flag contract (I-M4):
a run-level failure must return success=False so the caller can leave the URL
out of the progress file and retry it next run.

Run: python tests/test_ingest_web.py
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

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
