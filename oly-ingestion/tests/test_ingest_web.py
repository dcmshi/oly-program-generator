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


def test_403_is_transient_not_persisted():
    """audit3-L3 (ingestion): a WAF/rate-limit 403 marked the URL permanently
    ingested — the exact silent-drop ING-H1/audit2-L1 set out to prevent."""
    import ingest_web
    from ingest_web import fetch_article

    with patch.object(ingest_web.SESSION, "get", return_value=_mk_resp(status=403, raise_http=True)) as mock_get, \
         patch("time.sleep"):
        article, permanent = fetch_article("http://x/article/1/")
    assert article is None
    assert permanent is False, "403 is usually WAF/rate limiting — keep the URL pending"
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


# ── audit4-F1: CDX enumeration must retry and use https ──────────────────────

def test_cdx_503_is_retried():
    """audit4-F1: a transient Wayback 503 on the CDX call returned 0 URLs with
    no retry — and a real run then logs 'Nothing to ingest', masquerading as
    success. The enumeration must go through the shared retry helper."""
    import ingest_web
    from ingest_web import collect_charniga_urls

    with patch.object(ingest_web.SESSION, "get", return_value=_mk_resp(status=503, raise_http=True)) as mock_get, \
         patch("time.sleep"):
        pairs = collect_charniga_urls()
    assert pairs == []
    assert mock_get.call_count == 3, f"CDX 503 must be retried, got {mock_get.call_count} attempt(s)"


def test_wayback_urls_use_https():
    """audit4-F1: the plain-http CDX endpoint failed live where https succeeded
    first try."""
    from ingest_web import WAYBACK_CDX_URL, WAYBACK_RAW_FMT
    assert WAYBACK_CDX_URL.startswith("https://"), WAYBACK_CDX_URL
    assert WAYBACK_RAW_FMT.startswith("https://"), WAYBACK_RAW_FMT


# ── audit4-F7: numeric WP shortlinks must not pass the article filter ─────────

def test_article_regex_rejects_numeric_shortlinks():
    """audit4-F7: /YYYY/439/ style WP shortlinks (3+ digits) leaked through the
    1-2-digit month-archive lookahead — 6 of them in the live CDX probe."""
    from ingest_web import _CHARNIGA_ARTICLE_RE
    assert not _CHARNIGA_ARTICLE_RE.match("http://www.sportivnypress.com:80/2014/439/")
    assert not _CHARNIGA_ARTICLE_RE.match("https://sportivnypress.com/2016/2218/")
    # hyphenated numeric-looking slugs are real essays and must still pass
    assert _CHARNIGA_ARTICLE_RE.match("https://sportivnypress.com/2016/05/2017-review/")


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


# ── RAG-L11: article header boilerplate is stripped ──────────────────────────

def test_strip_article_header_removes_title_author_date_and_related_line():
    from ingest_web import strip_article_header

    body = ("Podcasts with Greg Everett\n\nGreg Everett\n\nJanuary 23, 2015\n\nSee Related Articles\n\n"
            "I thought I'd try to collect some of the podcast interviews.\n\nSecond paragraph.")
    out = strip_article_header(body, "Podcasts with Greg Everett", "Greg Everett")
    assert out.startswith("I thought I'd try")
    assert "See Related Articles" not in out and "January 23, 2015" not in out
    assert out.endswith("Second paragraph.")


def test_strip_article_header_only_touches_leading_lines():
    from ingest_web import strip_article_header

    body = "Real first paragraph.\n\nGreg Everett\n\nSee Related Articles\n\nMore text."
    assert strip_article_header(body, "Some Title", "Greg Everett") == body
    assert strip_article_header("", "T", "A") == ""


def test_fetch_article_output_starts_with_the_body(monkeypatch):
    import ingest_web as w

    html = ("<html><body><div class='sub_page_main_area_half_container_left'>"
            "<h1>Podcasts with Greg Everett</h1><p>Greg Everett</p><p>January 23, 2015</p><p>See Related Articles</p>"
            "<p>" + "Body sentence about snatch technique. " * 20 + "</p></div></body></html>")
    resp = MagicMock(text=html, status_code=200)
    monkeypatch.setattr(w, "_get_with_retry", lambda url, timeout=15, **k: (resp, False))
    art, permanent = w.fetch_article("https://www.catalystathletics.com/article/1/x/")
    assert art is not None and not permanent
    assert art["title"] == "Podcasts with Greg Everett" and art["author"] == "Greg Everett"
    assert art["text"].startswith("Body sentence about snatch technique.")


def test_bot_check_capture_falls_back_to_an_earlier_capture(monkeypatch):
    """CHARNIGA-STUBS: a Wayback capture of the site's bot-check interstitial
    triggers a CDX lookup for earlier captures; the first real one is used, and
    a URL whose captures are all the challenge page becomes a permanent skip."""
    from types import SimpleNamespace

    import ingest_web as mod

    challenge = b"<html><body><h1>One moment, please...</h1><p>Please wait while your request is being verified...</p></body></html>"
    real = ("<html><head><title>More About The Jerk - Sportivny Press</title></head><body>"
            "<h1 class='entry-title'>More About The Jerk</h1><div class='entry-content'>"
            + "".join(f"<p>Paragraph {i} about the jerk drive and the split receiving position.</p>" for i in range(12))
            + "</div></body></html>").encode()
    calls = []

    def fake_get(url, timeout=30, params=None):
        calls.append(url if params is None else (url, params["url"]))
        if params is not None:                                   # CDX lookup
            return SimpleNamespace(json=lambda: [["timestamp"], ["20240920003433"], ["20230101000000"], ["20220101000000"]]), False
        if "20240920003433" in url:
            return SimpleNamespace(content=challenge), False
        if "20230101000000" in url:
            return SimpleNamespace(content=challenge), False
        return SimpleNamespace(content=real), False

    monkeypatch.setattr(mod, "_get_with_retry", fake_get)
    article, permanent = mod.fetch_charniga_snapshot("https://www.sportivnypress.com/2014/more-about-the-jerk/", "20240920003433")
    assert article is not None and article["title"] == "More About The Jerk" and not permanent
    assert any(isinstance(c, tuple) for c in calls)              # CDX consulted once
    assert sum(1 for c in calls if isinstance(c, str)) == 3      # latest + 2023 (challenge) + 2022 (real)

    def all_challenge(url, timeout=30, params=None):
        if params is not None:
            return SimpleNamespace(json=lambda: [["timestamp"], ["20230101000000"]]), False
        return SimpleNamespace(content=challenge), False

    monkeypatch.setattr(mod, "_get_with_retry", all_challenge)
    article, permanent = mod.fetch_charniga_snapshot("https://www.sportivnypress.com/2014/x/", "20240920003433")
    assert article is None and permanent is True


def _wp_page(body_paragraphs: int, byline: bool = True) -> bytes:
    """A WordPress-shaped page: skip link, byline, article body, hidden
    related-post cards, teaser <article>s and a cookie banner."""
    paras = "".join(
        f"<p>Paragraph {i}: the taper reduces volume by forty to sixty percent while intensity is held for one to two weeks.</p>"
        for i in range(body_paragraphs)
    )
    return f"""<html><head><title>Tapering and Peaking: Why and How • Stronger by Science</title>
    <meta name="author" content="Brandon Roberts"></head>
    <body class="single content-sidebar"><a class="skip-link" href="#c">Skip to content</a>
    <header><nav><ul><li><a href="/">Home</a></li><li><a href="/articles">Articles</a></li></ul></nav></header>
    <main class="site-main"><div class="content-sidebar-wrap">
      <h1 class="entry-title">Tapering and  Peaking: Why and How</h1>
      <div class="post-meta">{'<span>by</span> <span class="author-name">Brandon Roberts</span>' if byline else ''}</div>
      <div class="entry-content">{paras}</div>
      <div class="featured-article-list"><div class="featured-article-item"><h5><a href="/x">Pillars of Front Squat</a></h5></div></div>
      <article class="elementor-post"><h3><a href="/y">High volume vs high intensity</a></h3><p>Milo Wolf</p></article>
      <ul><li><a href="/a">Related One</a></li><li><a href="/b"><img src="i.png"><span>Related Two</span></a></li></ul>
      <h2>Read Next</h2><p>Some teaser copy that should be cut.</p>
    </div></main>
    <div class="cookie-consent"><p>Manage Cookie Consent — we use technologies like cookies.</p></div>
    <footer><p>Scroll to Top</p></footer></body></html>""".encode()


def test_fetch_generic_article_keeps_the_body_and_drops_the_chrome(monkeypatch):
    """--site urls: the article body survives a theme wrapper whose class matches
    the junk regex, while skip links, byline, related cards, link-only lists,
    teaser <article>s, the trailing "Read Next" block and the cookie banner go.
    The author comes from the meta tag when the list gives none."""
    from types import SimpleNamespace

    import ingest_web as mod

    monkeypatch.setattr(mod, "_get_with_retry", lambda url, timeout=30, params=None: (SimpleNamespace(text=_wp_page(30).decode()), False))
    article, permanent = mod.fetch_generic_article("https://www.strongerbyscience.com/tapering/")
    assert article is not None and not permanent
    assert article["title"] == "Tapering and Peaking: Why and How"
    assert article["author"] == "Brandon Roberts"
    text = article["text"]
    assert text.startswith("Paragraph 0:")
    assert text.count("Paragraph ") == 30
    for junk in ("Skip to content", "Brandon Roberts", "Pillars of Front Squat", "Related One", "Related Two",
                 "Milo Wolf", "Read Next", "teaser copy", "Cookie Consent", "Scroll to Top", "Home"):
        assert junk not in text, junk

    # a list-level author wins over the page's meta tag
    article, _ = mod.fetch_generic_article("https://www.strongerbyscience.com/tapering/", "Greg Nuckols")
    assert article["author"] == "Greg Nuckols"


def test_fetch_generic_article_skips_video_landing_pages(monkeypatch):
    """A page whose body is a blurb plus teaser cards is under GENERIC_MIN_WORDS
    and a permanent skip — JTS's video posts must not become 300-word sources."""
    from types import SimpleNamespace

    import ingest_web as mod

    monkeypatch.setattr(mod, "_get_with_retry", lambda url, timeout=30, params=None: (SimpleNamespace(text=_wp_page(3).decode()), False))
    article, permanent = mod.fetch_generic_article("https://www.jtsstrength.com/programming-for-weightlifting/")
    assert article is None and permanent is True


def test_load_url_list_keeps_order_dedupes_and_overrides_author(tmp_path):
    import json

    from ingest_web import load_url_list

    f = tmp_path / "list.json"
    f.write_text(json.dumps({"author": "Glenn Pendlay", "urls": [
        "https://a.example/one/", {"url": "https://a.example/two/", "author": "Bo Sandoval"}, "https://a.example/one/",
    ]}), encoding="utf-8")
    assert load_url_list(f) == [("https://a.example/one/", "Glenn Pendlay"), ("https://a.example/two/", "Bo Sandoval")]


def test_research_profile_maps_the_pritchard_sources():
    """CORPUS.md rows 3–4: the `research` profile exists and the three Pritchard
    titles resolve to it instead of the silent `programming` fallback."""
    from processors.chunker import CHUNK_PROFILES, SemanticChunker, SourceProfile

    assert SourceProfile.RESEARCH in CHUNK_PROFILES
    for title in ("Tapering Strategies to Enhance Maximal Strength",
                  "Short-term training cessation as a method of tapering to improve maximal strength",
                  "Higher vs lower intensity strength training taper effects on neuromuscular performance"):
        assert SemanticChunker.for_source(title).source_profile is SourceProfile.RESEARCH, title


# ── I-L11: the web path shares pipeline.py's section routing ──────────────────

_PROSE = ("The snatch pull is trained in the accumulation block with moderate loads, "
          "because the athlete needs positional strength before speed work. ") * 12


def _prose_section(content=_PROSE):
    from processors.classifier import ClassifiedSection, ContentType
    return ClassifiedSection(content=content, content_type=ContentType.PROSE, metadata={})


def test_web_path_validates_chunks():
    """The web path used to skip validate_chunk (the gap behind I-H1); it now
    runs through SectionProcessor.process_prose like the book path."""
    comps = _components()
    comps["settings"].validate_chunks = True
    comps["settings"].quarantine_invalid_chunks = False
    comps["classifier"].classify_sections.return_value = [_prose_section()]
    with patch("processors.section_processor.validate_chunk", wraps=__import__(
            "processors.chunker", fromlist=["validate_chunk"]).validate_chunk) as vc:
        _, ok = ingest_article(_ARTICLE, comps, _stats())
    assert ok is True
    assert vc.call_count >= 1


def test_web_path_records_the_shared_stats_shape():
    comps = _components()
    comps["settings"].validate_chunks = False
    comps["classifier"].classify_sections.return_value = [_prose_section()]
    stats = _stats()
    ingest_article(_ARTICLE, comps, stats)
    result = comps["structured_loader"].complete_run.call_args.args[1]
    for key in ("prose_chunks", "prose_chunks_valid", "chunks_loaded", "chunks_skipped_dedup", "principles"):
        assert key in result, key
    assert result["chunks_loaded"] == stats["chunks_total"] >= 1


def test_web_path_runs_quarantine_pass_only_when_enabled():
    for enabled, expected in ((True, 1), (False, 0)):
        comps = _components()
        comps["settings"].validate_chunks = False
        comps["classifier"].classify_sections.return_value = [_prose_section()]
        comps["quarantine"] = enabled
        with patch("ingest_web.run_quarantine_pass", return_value=0) as q:
            ingest_article(_ARTICLE, comps, _stats())
        assert q.call_count == expected, enabled


def test_web_section_error_rolls_back_and_continues():
    """A failing section is rolled back and the next one still loads."""
    comps = _components()
    comps["settings"].validate_chunks = False
    comps["vector_loader"].load_chunks.side_effect = [RuntimeError("bad"), 1]
    comps["classifier"].classify_sections.return_value = [_prose_section(), _prose_section()]
    _, ok = ingest_article(_ARTICLE, comps, _stats())
    assert ok is True
    comps["vector_loader"].conn.rollback.assert_called_once()
    assert comps["structured_loader"].complete_run.call_args.args[1]["chunks_loaded"] == 1


def test_principle_section_goes_to_extractor_not_vector_store():
    from processors.classifier import ClassifiedSection, ContentType
    comps = _components()
    comps["principle_extractor"].extract.return_value = ["p1", "p2"]
    comps["classifier"].classify_sections.return_value = [
        ClassifiedSection(content="If the athlete misses, reduce 5%.", content_type=ContentType.PRINCIPLE, metadata={})
    ]
    stats = _stats()
    ingest_article(_ARTICLE, comps, stats)
    comps["vector_loader"].load_chunks.assert_not_called()
    assert stats["principles_total"] == 2


def test_web_path_runs_principle_audit_on_the_article_text():
    from processors.classifier import ClassifiedSection, ContentType
    comps = _components()
    comps["principle_extractor"].extract.return_value = ["p1"]
    comps["classifier"].classify_sections.return_value = [
        ClassifiedSection(content="If the athlete misses, reduce 5%.", content_type=ContentType.PRINCIPLE, metadata={})
    ]
    comps["principle_audit"] = True
    with patch("principle_audit.run_audit_pass", return_value={"claims": 1}) as audit:
        ingest_article(_ARTICLE, comps, _stats())
    assert audit.call_args.args[0] == 1 and audit.call_args.args[2] == _ARTICLE["text"]
    comps["principle_audit"] = False
    with patch("principle_audit.run_audit_pass") as audit:
        ingest_article(_ARTICLE, comps, _stats())
    audit.assert_not_called()
