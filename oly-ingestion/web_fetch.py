# web_fetch.py
"""
Polite web fetching for every ingestion path (WEB-POLITE), split out of
ingest_web.py (CLEAN-4) so fetchers that aren't web ingests — Europe PMC JATS
(extractors/jats_extractor.py, CLEAN-6), the principle audit, the accessory
miner — use it without importing the ingest CLI.

`_get_with_retry(url, cache=…)` is the one GET: robots.txt for BOT_UA_TOKEN,
per-host spacing, Retry-After, a per-run circuit breaker and the on-disk page
cache, all active once a CLI has called `configure_web_fetching()`.
ingest_web re-exports these names, so `ingest_web._get_with_retry` and
friends keep working; patch `web_fetch.SESSION` / `web_fetch._policy` to
change what the fetcher itself sees.
"""

import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests

logger = logging.getLogger(__name__)

BOT_UA_TOKEN = "OlyIngestionBot"          # the product token robots.txt rules are matched against
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": f"Mozilla/5.0 (compatible; {BOT_UA_TOKEN}/1.0; research use)"
})

# ── Polite fetching (WEB-POLITE) ───────────────────────────────
# catalystathletics.com stopped answering this machine on 2026-10-12 (TCP
# connect timeouts while every other site answered) after repeated full-site
# reads: the crawl, `principle_audit.py --fetch-web` (no delay between
# requests) and every `principle_model_compare` run, none of them cached. Every
# request now goes through `_get_with_retry`'s policy: robots.txt for
# BOT_UA_TOKEN, at least WEB_MIN_REQUEST_INTERVAL_S between requests to one
# host (the robots Crawl-delay when longer), and a per-run circuit breaker — a
# host whose failures persist through the retries is not contacted again this
# run. Article pages are cached on disk (`_get_with_retry(cache=True)`), so a
# re-run, an audit or an eval reads the copy instead of the site. The policy
# is off until a CLI calls `configure_web_fetching()`, which keeps library
# callers and the unit tests from sleeping or writing a cache.
WEB_CACHE_DIR = Path(__file__).parent / "sources" / ".web_cache"   # gitignored with the sources
WEB_MIN_REQUEST_INTERVAL_S = 3.0
RETRY_AFTER_MAX_S = 300.0          # longest Retry-After we wait out; beyond it the host is left for the next run


class _WebPolicy:
    def __init__(self):
        self.enabled = False
        self.min_interval_s = 0.0
        self.cache_dir: Path | None = None
        self.last_request: dict[str, float] = {}
        self.robots: dict[str, RobotFileParser | None] = {}
        self.down_hosts: set[str] = set()


_policy = _WebPolicy()


def configure_web_fetching(*, cache_dir: Path | None = WEB_CACHE_DIR,
                           min_interval_s: float = WEB_MIN_REQUEST_INTERVAL_S) -> None:
    """Turn the polite-fetching policy on for this process (CLIs call this
    first). `cache_dir=None` fetches every page live."""
    global _policy
    _policy = _WebPolicy()
    _policy.enabled = True
    _policy.min_interval_s = min_interval_s
    _policy.cache_dir = cache_dir


def _host(url: str) -> str:
    return urlsplit(url).netloc.lower()


def _throttle(host: str) -> None:
    rp = _policy.robots.get(host)
    interval = max(_policy.min_interval_s, float((rp.crawl_delay(BOT_UA_TOKEN) if rp else None) or 0))
    wait = _policy.last_request.get(host, 0.0) + interval - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _policy.last_request[host] = time.monotonic()


def _robots_for(url: str) -> RobotFileParser | None:
    """The host's parsed robots.txt, fetched once per run. None = no rules
    (a 4xx robots.txt, RFC 9309 §2.3.1.3); a 5xx disallows everything for this
    run (§2.3.1.4), and an unreachable host goes on the down list."""
    host = _host(url)
    if host in _policy.robots:
        return _policy.robots[host]
    parts = urlsplit(url)
    rp = RobotFileParser()
    _throttle(host)
    try:
        resp = SESSION.get(f"{parts.scheme}://{parts.netloc}/robots.txt", timeout=15)
        if resp.status_code >= 500 or resp.status_code == 429:
            # 429 is a rate limit, not "no robots.txt" — back off like a 5xx
            rp.disallow_all = True
            if resp.status_code == 429:
                logger.warning(f"robots.txt rate-limited (429) for {host} — no requests to it this run")
                _policy.down_hosts.add(host)
        elif resp.status_code >= 400:
            rp = None
        else:
            rp.parse(resp.text.splitlines())
    except requests.RequestException as e:
        logger.warning(f"robots.txt unreachable for {host} ({e}) — no requests to it this run")
        _policy.down_hosts.add(host)
        rp.disallow_all = True
    _policy.robots[host] = rp
    return rp


def _cache_file(url: str, params: dict | None) -> Path | None:
    if not (_policy.enabled and _policy.cache_dir):
        return None
    key = url + ("?" + json.dumps(params, sort_keys=True) if params else "")
    return _policy.cache_dir / f"{hashlib.sha256(key.encode()).hexdigest()}.json"


class _CachedResponse:
    """The parts of a requests.Response the fetchers read."""
    status_code = 200

    def __init__(self, url: str, text: str, encoding: str | None):
        self.url, self.text, self.encoding = url, text, encoding
        self.content = text.encode(encoding or "utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)


# Transient statuses (rate limit / server-side) must not be conflated with
# permanent failures (404) when deciding whether to persist a URL as done.
# 403: usually WAF/rate-limiting for a bot UA, not a real denial — persisting
# it silently dropped the article forever (audit3-L3). 408: request timeout.
_RETRYABLE_HTTP_STATUSES = {403, 408, 429, 500, 502, 503, 504}


def _retry_after_s(resp) -> float | None:
    """Seconds a 429/503 asks us to wait (Retry-After: delta-seconds or an
    HTTP date); None when absent or unparseable."""
    value = (getattr(resp, "headers", None) or {}).get("Retry-After")
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        from email.utils import parsedate_to_datetime
        return max(0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError):
        return None


def _get_with_retry(url: str, timeout: int = 30, attempts: int = 3, params: dict | None = None,
                    cache: bool = False):
    """GET with exponential backoff on transient failures (Wayback + live sites).

    Returns (response | None, permanent). permanent=True only for HTTP errors
    that will never succeed (404 etc.); rate limits, 5xx, timeouts and
    connection drops are retried and, if they persist, reported as transient so
    the caller keeps the URL pending for the next run (ING-H1, audit2-L1).
    Under `configure_web_fetching()` (WEB-POLITE) a URL robots.txt disallows is
    permanent, a host that already failed this run is transient without a
    request, and `cache=True` (article pages — never listings or CDX queries,
    which change) answers from / writes to the on-disk page cache.
    """
    cache_file = _cache_file(url, params) if cache else None
    if cache_file and cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            return _CachedResponse(data["url"], data["text"], data.get("encoding")), False
        except (OSError, ValueError, KeyError) as e:
            logger.warning(f"Web cache entry unreadable ({e}) — refetching {url}")
    host = _host(url)
    if _policy.enabled:
        rp = None if host in _policy.down_hosts else _robots_for(url)
        if host in _policy.down_hosts:
            logger.info(f"Skipping {url}: {host} is not answering this run")
            return None, False
        if rp is not None and not rp.can_fetch(BOT_UA_TOKEN, url):
            logger.warning(f"robots.txt disallows {url} for {BOT_UA_TOKEN} — skipping")
            return None, True
    err = None
    for attempt in range(1, attempts + 1):
        try:
            if _policy.enabled:
                _throttle(host)
            resp = SESSION.get(url, timeout=timeout, params=params)
            resp.raise_for_status()
            if cache_file:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps({
                    "url": url, "params": params, "encoding": resp.encoding, "text": resp.text,
                    "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                }, ensure_ascii=False), encoding="utf-8")
            return resp, False
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status not in _RETRYABLE_HTTP_STATUSES:
                logger.warning(f"Permanent HTTP {status} for {url}")
                return None, True
            err = e
            retry_after = _retry_after_s(e.response)
        except requests.RequestException as e:
            err = e
            retry_after = None
        if attempt < attempts:
            if retry_after is not None and retry_after > RETRY_AFTER_MAX_S:
                logger.warning(f"{host} asks for a {retry_after:.0f}s pause — leaving it for the next run")
                break
            wait = max(2 ** attempt, retry_after or 0)
            logger.warning(f"Fetch failed ({err}); retrying in {wait}s ({attempt}/{attempts})")
            time.sleep(wait)
    logger.warning(f"Transient failure persists for {url} — leaving pending for next run")
    if _policy.enabled:
        logger.warning(f"{host} is not answering — no more requests to it this run")
        _policy.down_hosts.add(host)
    return None, False
