#!/usr/bin/env python3
"""
Audit extracted principles for numbers the source never states (PRIN-AUDIT).

Principle extraction fills the structured `recommendation` with limit-like
numbers (`intensity_ceiling`, `total_reps_max`, `volume_modifier`, …) and the
session prompt shows principles as `name: recommendation` — so a number the
model invented reaches generation as an instruction. On the Cissik summary
(source 808) 14 of 52 rows carried one (juvenile ceiling 85 %, ≤ 6 sessions a
week, GPP shares filed as volume modifiers).

A claim is *supported* when the value — or an equivalent form of it — occurs as
a number in the source's text:

  * volume_modifier v: v, v×100 ("60 %") or (1−v)×100 ("reduce by 40 %")
  * rest_between_sets_min s: s or s/60 (minutes)
  * fractional comparisons (recent_make_rate 0.7): v or v×100
  * a range "a–b" in the text also supports its midpoint
  * number words (one … twenty, once, twice) count as numbers

Source text is every chunk the source contains (`chunk_sources`, quarantined
rows included) plus, for books and papers, the full extracted file text from
`sources/` (sections classified PRINCIPLE are extracted but never chunked).
Whole-source matching is lenient — a common number like 80 occurs somewhere
in any book — so "unsupported" is a lower bound on invention, and a flag is
strong evidence. `--apply` removes only unsupported *recommendation* keys; a
condition key is reported, never dropped (dropping one would make a
conditional rule unconditional).

Usage (from oly-ingestion/):
    PYTHONUTF8=1 uv run python principle_audit.py --report               # per-model summary, no writes
    PYTHONUTF8=1 uv run python principle_audit.py --report --source-id 808 --show
    PYTHONUTF8=1 uv run python principle_audit.py --report --fetch-web logs/prin_audit_web.json
    PYTHONUTF8=1 uv run python principle_audit.py --apply --fetch-web logs/prin_audit_web.json --backup logs/prin_audit_backup.json

Web sources: sections classified PRINCIPLE were never chunked, so chunk text
alone under-covers an article. `--fetch-web` re-fetches the flagged and
text-less articles (Catalyst live, Charniga from the Wayback Machine, the rest
generic) and judges them against the full page. A source with no text at all
is reported as unverifiable and never changed.
"""

import argparse
import json
import logging
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))  # repo root for shared.*

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# The numeric recommendation keys of principle_extractor.PRINCIPLE_SCHEMA
NUMERIC_REC_KEYS: tuple[str, ...] = (
    "volume_modifier", "total_reps_max", "intensity_floor", "intensity_ceiling",
    "sessions_per_week_max", "competition_lift_frequency", "rest_between_sets_min",
    "deload_frequency_weeks",
)
COMPARISON_COND_KEYS: tuple[str, ...] = (
    "weeks_out_from_competition", "training_age_years", "week_of_block",
    "recent_make_rate", "rpe_average_last_week",
)

_NUMBER_WORDS = {
    "zero": 0, "one": 1, "once": 1, "two": 2, "twice": 2, "three": 3, "thrice": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "ninety": 90, "half": 0.5,
}
_NUM = r"\d+(?:\.\d+)?"
_NUMBER_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\d])")
_RANGE_RE = re.compile(rf"({_NUM})\s*%?\s*(?:-|–|—|to)\s*({_NUM})")
_WORD_RE = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", re.IGNORECASE)


@dataclass(frozen=True)
class Claim:
    field: str          # "recommendation" | "condition"
    key: str
    value: float


def _key(v: float) -> float:
    return round(float(v), 2)


def text_numbers(text: str) -> set[float]:
    """Every number the text states, plus midpoints of stated ranges."""
    nums: set[float] = set()
    for m in _NUMBER_RE.finditer(text):
        nums.add(_key(m.group(1).replace(",", "")))
    for m in _RANGE_RE.finditer(text):
        a, b = float(m.group(1)), float(m.group(2))
        nums.add(_key((a + b) / 2))
    for m in _WORD_RE.finditer(text):
        nums.add(_key(_NUMBER_WORDS[m.group(1).lower()]))
    return nums


def numeric_claims(recommendation: dict, condition: dict) -> list[Claim]:
    """The numbers a principle asserts: numeric recommendation values and the
    operands of its comparison conditions (`{"lte": 2}`, `{"between": [3, 5]}`)."""
    claims: list[Claim] = []
    for key in NUMERIC_REC_KEYS:
        v = (recommendation or {}).get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            claims.append(Claim("recommendation", key, float(v)))
    for key in COMPARISON_COND_KEYS:
        comp = (condition or {}).get(key)
        if not isinstance(comp, dict):
            continue
        for operand in comp.values():
            for v in operand if isinstance(operand, list) else [operand]:
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    claims.append(Claim("condition", key, float(v)))
    return claims


def claim_forms(claim: Claim) -> set[float]:
    """Values whose presence in the text supports the claim."""
    v = claim.value
    forms = {v}
    if claim.key == "volume_modifier":
        forms |= {v * 100, (1 - v) * 100}
    elif claim.key == "rest_between_sets_min":
        forms.add(v / 60)
    elif 0 < v < 1:                      # recent_make_rate 0.7 ↔ "70 %"
        forms.add(v * 100)
    return {_key(f) for f in forms}


def is_supported(claim: Claim, numbers: set[float]) -> bool:
    # weeks_out_from_competition 0 is "competition day / week" — a state the
    # text names in words (warm-up protocols, the meet itself), not a number.
    if claim.key == "weeks_out_from_competition" and claim.value == 0:
        return True
    return bool(claim_forms(claim) & numbers)


def audit_principle(recommendation: dict, condition: dict, numbers: set[float]) -> tuple[list[Claim], list[Claim]]:
    """(supported claims, unsupported claims) for one principle."""
    sup, unsup = [], []
    for c in numeric_claims(recommendation, condition):
        (sup if is_supported(c, numbers) else unsup).append(c)
    return sup, unsup


def strip_unsupported(recommendation: dict, unsupported: list[Claim]) -> dict:
    """The recommendation without its unsupported numeric keys."""
    drop = {c.key for c in unsupported if c.field == "recommendation"}
    return {k: v for k, v in recommendation.items() if k not in drop}


# ── Source text ───────────────────────────────────────────────────────────

# source_id → file-name prefix under sources/ (docs/CORPUS.md "Files on disk").
# Explicit rather than fuzzy: title matching paired Bompa with two research
# papers and the never-obtained *A System of…* row with the *Program of…* scan.
SOURCE_FILES: dict[int, str] = {
    2: "Bob Takano - ", 51: "Vladimir Zatsiorsky", 52: "Arthur Drechsler - ",
    499: "N.P. Laputin", 501: "A.S. Medvedev - A Program", 502: "Greg Everett - Olympic Weightlifting for Sports",
    504: "Mike Israetel - ", 505: "Kelly Starrett - ", 506: "Dan John - ",
    507: "Greg Everett - Olympic Weightlifting_ A Complete Guide",
    742: "Pritchard 2017 - ", 743: "Pritchard 2018 - ", 744: "Pritchard 2019 - ", 745: "Winwood 2026 - ",
    746: "Travis 2021 - ", 747: "Hornsby 2017 - ", 748: "Suarez 2019 - ", 749: "Huebner 2022 - ",
    750: "Soriano 2019 - ", 751: "Stavropoulos 2025 - ", 752: "Huebner 2019 - ", 794: "Suchomel 2015 - ",
    795: "Suchomel 2021 - ", 796: "Stone 2021 - ", 797: "DeWeese 2015 - The training process part 1",
    798: "DeWeese 2015 - The training process part 2",
    799: "R.A. Roman - ", 800: "Y.V. Verkhoshansky - ", 801: "A.N. Vorobyev - ", 802: "Tudor Bompa",
    803: "Ilya Zhekov", 804: "Andrew Charniga - Weightlifting Training and Biomechanics",
    805: "Andrew Charniga - There Is No System", 806: "Andrew Charniga - A De-Masculinization",
    807: "Tommy Kono - ",
}


def match_source_files(source_id: int, files: list[Path]) -> list[Path]:
    """The files on disk for a source (every file whose name starts with its prefix)."""
    prefix = SOURCE_FILES.get(source_id)
    return [f for f in files if prefix and f.name.startswith(prefix)]


def file_text(path: Path) -> str:
    """Full text of a source file — EPUB chapters, PDF text layer (or the OCR
    cache when the PDF is a scan), plain text. Never calls a model."""
    if path.suffix == ".epub":
        from extractors.epub_extractor import extract_text_from_epub
        return "\n\n".join(extract_text_from_epub(path))
    if path.suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".pdf":
        import fitz
        with fitz.open(path) as doc:
            text = "\n\n".join(page.get_text() for page in doc)
            n_pages = doc.page_count
        if len(text.strip()) < 10 * max(1, n_pages):     # < 10 chars/page: a scan — use the OCR cache
            from extractors.ocr_cache import CACHE_DIRNAME, file_sha256
            cache = path.parent / CACHE_DIRNAME / f"{file_sha256(path)}.json"
            if not cache.exists():
                cache = Path(__file__).parent / "sources" / CACHE_DIRNAME / f"{file_sha256(path)}.json"
            if cache.exists():
                pages = json.loads(cache.read_text(encoding="utf-8")).get("pages", {})
                text += "\n\n" + "\n\n".join(pages[k] for k in sorted(pages, key=int))
        return text
    return ""


def load_source_texts(cur, source_files: list[Path], only: list[int] | None = None) -> dict[int, str]:
    """{source_id: all its chunk text + matched file text}."""
    cur.execute("""
        SELECT cs.source_id, string_agg(k.raw_content, E'\\n\\n')
        FROM chunk_sources cs JOIN knowledge_chunks k ON k.id = cs.chunk_id
        GROUP BY cs.source_id
    """)
    texts = {sid: txt or "" for sid, txt in cur.fetchall()}
    for sid in SOURCE_FILES:
        if only and sid not in only:
            continue
        for f in match_source_files(sid, source_files):
            try:
                texts[sid] = texts.get(sid, "") + "\n\n" + file_text(f)
            except Exception as e:                       # noqa: BLE001 — audit, keep going
                logger.warning(f"Could not read {f.name}: {e}")
    return texts


def fetch_web_text(url: str) -> str | None:
    """The article text for a web source, through the same fetchers the ingest
    used (Catalyst selectors, Wayback for Charniga, generic WordPress for the
    rest). None when the page can't be fetched."""
    import ingest_web as w
    if "catalystathletics.com" in url:
        article, _ = w.fetch_article(url)
    elif "sportivnypress.com" in url:
        # Wayback resolves a year-only timestamp to the nearest capture
        article, _ = w.fetch_charniga_snapshot(url, "2024")
    else:
        article, _ = w.fetch_generic_article(url)
    return article["text"] if article else None


def add_web_texts(cur, texts: dict[int, str], source_ids: set[int], cache_path: Path) -> int:
    """Append the fetched article text for these web sources (cached on disk,
    so a re-run doesn't hit the sites again). Returns how many have text."""
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    cur.execute("SELECT id, url FROM sources WHERE id = ANY(%s) AND url IS NOT NULL", (sorted(source_ids),))
    got = 0
    for sid, url in cur.fetchall():
        key = str(sid)
        if key not in cache:
            try:
                cache[key] = fetch_web_text(url)
            except Exception as e:                       # noqa: BLE001 — audit, keep going
                logger.warning(f"Fetch failed for source {sid} ({url}): {e}")
                cache[key] = None
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache), encoding="utf-8")
        if cache[key]:
            texts[sid] = texts.get(sid, "") + "\n\n" + cache[key]
            got += 1
    return got


def evaluate(rows, texts: dict[int, str]):
    """(stats by "model · source type", recommendation changes, flagged source ids, lines).
    A source with no text at all is *unverifiable*: counted, never flagged."""
    stats = defaultdict(lambda: defaultdict(int))
    changes: list[dict] = []
    flagged: set[int] = set()
    lines: list[str] = []
    numbers_cache: dict[int, set[float]] = {}
    for pid, sid, name, rec, cond, model, stype in rows:
        # Grouped by source type too: whole-book text is far more lenient than
        # one web article's, so model rates are only comparable within a type.
        s = stats[f"{model} · {stype}"]
        s["principles"] += 1
        if not texts.get(sid, "").strip():
            s["unverifiable"] += 1
            continue
        if sid not in numbers_cache:
            numbers_cache[sid] = text_numbers(texts[sid])
        sup, unsup = audit_principle(rec or {}, cond or {}, numbers_cache[sid])
        s["with_numbers"] += bool(sup or unsup)
        s["claims"] += len(sup) + len(unsup)
        s["unsupported_claims"] += len(unsup)
        s["principles_with_unsupported"] += bool(unsup)
        if unsup:
            flagged.add(sid)
            lines.append(f"  #{pid} [{sid}] {name}: " + ", ".join(f"{c.field}.{c.key}={c.value:g}" for c in unsup))
            new_rec = strip_unsupported(rec or {}, unsup)
            if new_rec != (rec or {}):
                changes.append({"id": pid, "before": rec, "after": new_rec})
    return stats, changes, flagged, lines


def _source_files() -> list[Path]:
    root = Path(__file__).parent / "sources"
    return [p for p in root.rglob("*") if p.suffix in (".pdf", ".epub", ".txt") and ".ocr_cache" not in p.parts]


def main() -> None:
    import psycopg2
    from config import Settings

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--report", action="store_true", help="summarise; write nothing")
    ap.add_argument("--apply", action="store_true", help="remove unsupported recommendation keys")
    ap.add_argument("--backup", type=Path, help="with --apply: JSON file for the pre-change recommendations")
    ap.add_argument("--source-id", type=int, action="append", help="limit to these sources (repeatable)")
    ap.add_argument("--show", action="store_true", help="list each unsupported claim")
    ap.add_argument("--fetch-web", type=Path, metavar="CACHE_JSON",
                    help="re-fetch flagged / text-less web articles (cached in this file) before judging them")
    args = ap.parse_args()
    if args.apply == args.report:
        ap.error("pass exactly one of --report / --apply")
    if args.apply and not args.backup:
        ap.error("--apply needs --backup")

    settings = Settings()
    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    texts = load_source_texts(cur, _source_files(), args.source_id)

    where, params = "", []
    if args.source_id:
        where, params = "WHERE p.source_id = ANY(%s)", [args.source_id]
    cur.execute(f"""
        SELECT p.id, p.source_id, p.principle_name, p.recommendation, p.condition,
               coalesce((SELECT r.config_snapshot->>'llm_model' FROM ingestion_runs r
                         WHERE r.source_id = p.source_id AND r.status::text = 'completed'
                         ORDER BY r.id DESC LIMIT 1), '?') AS model,
               s.source_type::text
        FROM programming_principles p JOIN sources s ON s.id = p.source_id {where}
        ORDER BY p.id
    """, params)
    rows = cur.fetchall()

    stats, changes, flagged, lines = evaluate(rows, texts)
    if args.fetch_web:
        # Web sections classified PRINCIPLE were never chunked, so a web flag
        # may just be text the chunks don't hold — re-check against the article.
        cur.execute("SELECT id FROM sources WHERE source_type::text = 'website'")
        web = {r[0] for r in cur.fetchall()} & (flagged | {r[1] for r in rows if not texts.get(r[1], "").strip()})
        got = add_web_texts(cur, texts, web, args.fetch_web)
        logger.info(f"Fetched article text for {got} of {len(web)} flagged / text-less web sources")
        stats, changes, flagged, lines = evaluate(rows, texts)
    if args.show:
        print("\n".join(lines))

    print(f"\n{'model (last run) · source type':<44} {'princ':>6} {'unverif':>8} {'w/nums':>7} {'claims':>7} {'unsup':>6} {'rate':>6} {'princ w/ unsup':>15}")
    for model, s in sorted(stats.items(), key=lambda kv: -kv[1]["principles"]):
        rate = s["unsupported_claims"] / s["claims"] if s["claims"] else 0.0
        print(f"{model:<44} {s['principles']:>6} {s['unverifiable']:>8} {s['with_numbers']:>7} {s['claims']:>7} "
              f"{s['unsupported_claims']:>6} {rate:>6.1%} {s['principles_with_unsupported']:>15}")
    print(f"\nrecommendation rows that --apply would change: {len(changes)}")

    if args.apply and changes:
        args.backup.parent.mkdir(parents=True, exist_ok=True)
        args.backup.write_text(json.dumps(changes, indent=1), encoding="utf-8")
        for ch in changes:
            cur.execute("UPDATE programming_principles SET recommendation = %s WHERE id = %s",
                        (json.dumps(ch["after"]), ch["id"]))
        conn.commit()
        print(f"applied; backup of the previous values in {args.backup}")
    conn.close()


if __name__ == "__main__":
    main()
