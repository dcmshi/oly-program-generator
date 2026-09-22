"""One-off ingest (2026-09-22): John Cissik's blog summary of Medvedyev's
*A System of Multi-Year Training in Weightlifting* — a stopgap for the book
itself (docs/CORPUS.md planned-additions row 8), source_id 808.

The post's seven tables are images, which the generic extractor drops, so each
<img> is replaced by a hand transcription before extraction (checked by eye
against the PNGs; every percentage table sums to 100). A plain
`ingest_web.py --site urls` run would lose them — re-ingest with this script.
Run from oly-ingestion/:

    PYTHONPATH=. PYTHONUTF8=1 uv run python sources/url_lists/cissik_medvedyev.py           # print text only
    PYTHONPATH=. PYTHONUTF8=1 uv run python sources/url_lists/cissik_medvedyev.py --ingest  # ingest (contextualized)
"""
import sys

import ingest_web as w
from bs4 import BeautifulSoup

URL = "https://www.cissik.com/blog/2026/09/a-system-of-multi-year-training-in-weightlifting/"
AUTHOR = "John Cissik"

T = "\t"
TABLES = {
    "Weight-class-kg-Height-cm.png": [
        "Table: Medvedyev's recommended height for each weight class",
        f"Weight class (kg){T}Height (cm)",
        *[f"{a}{T}{b}" for a, b in [("52", "149"), ("56", "153"), ("60", "159"), ("67.5", "164"), ("75", "168.5"),
                                    ("82.5", "172.5"), ("90", "176"), ("100", "178"), ("110", "181"), ("110+", "185")]],
    ],
    "Exercise-order-preparatory-phase.png": [
        "Table: Exercise order within a workout",
        "Preparatory phase: parts of the snatch / clean and jerk → pulls → squats and other strength exercises.",
        "Competition phase: snatch or power snatch / clean and jerk or power clean → parts of the snatch / clean and jerk → pulls → squats and other strength exercises.",
    ],
    "First-workout.png": [
        "Table: Sample first four workouts for a beginner learning the snatch (P. = power, h = hang, AK = above the knee, BK = below the knee)",
        "First workout: 1. P. Snatch, h, AK; 2. Snatch pull to knee level.",
        "Second workout: 1. P. Snatch, h, BK; 2. Snatch pull to knee level; 3. Snatch pull.",
        "Third workout: 1. P. Snatch; 2. Snatch pull to knee level; 3. P. Snatch + overhead squat; 4. Snatch pull + power snatch.",
        "Fourth workout: 1. P. Snatch + overhead squat; 2. Classic snatch; 3. Snatch pull; 4. Snatch pull to knee level.",
    ],
    "Beginner.png": [
        "Table: Number of lifts (NL), beginner vs qualified athlete",
        f"Period{T}Beginner{T}Qualified athlete",
        f"Preparatory period{T}650{T}1500",
        f"Competition period{T}650{T}850",
    ],
    "NLsmonth-average.png": [
        "Table: Average number of lifts (NL) per month by sports classification",
        f"Class{T}NL per month (average)",
        f"Class III{T}986", f"Class II{T}1069", f"Class I{T}1094", f"CMS{T}1169", f"MS{T}1306",
    ],
    "VariantWeek.png": [
        "Table: Pre-competition loading variants — percentage of the monthly volume (NL) done each week; week 4 = four weeks out, week 1 = the competition week",
        f"Variant{T}Week 4{T}Week 3{T}Week 2{T}Week 1",
        f"A{T}26%{T}35%{T}23%{T}16%",
        f"B{T}36%{T}28%{T}21%{T}15%",
        f"C{T}24%{T}38%{T}25%{T}13%",
    ],
    "Competition-Period.png": [
        "Table: Share of lifts in all exercises by intensity zone (% of 1RM), bands as printed in the source",
        f"Period{T}<70%{T}70–75%{T}80–85%{T}>90%",
        f"Preparatory period{T}25%{T}30%{T}40%{T}5%",
        f"Competition period{T}20%{T}25%{T}42%{T}13%",
    ],
}


def patched_html() -> str:
    resp, _ = w._get_with_retry(URL)
    assert resp is not None, f"could not fetch {URL}"
    soup = BeautifulSoup(resp.text, "html.parser")
    done = set()
    for img in soup.find_all("img"):
        name = img.get("src", "").rsplit("/", 1)[-1]
        if name in TABLES:
            p = soup.new_tag("p")
            p.string = "\n".join(TABLES[name])
            (img.find_parent("figure") or img).replace_with(p)
            done.add(name)
    assert done == set(TABLES), set(TABLES) - done
    return str(soup)


def build_article() -> dict:
    html = patched_html()
    orig = w._get_with_retry              # serve the patched page to the normal extractor
    w._get_with_retry = lambda url, timeout=30, params=None, attempts=3: (type("R", (), {"text": html})(), False)
    try:
        article, _ = w.fetch_generic_article(URL, AUTHOR)
    finally:
        w._get_with_retry = orig
    assert article is not None
    article["title"] = "A System of Multi-Year Training in Weightlifting (Medvedyev) — John Cissik's summary"
    for rows in TABLES.values():
        assert rows[0] in article["text"], rows[0]
    return article


if __name__ == "__main__":
    article = build_article()
    if "--ingest" not in sys.argv:
        print(article["title"], "|", article["author"], "|", len(article["text"].split()), "words\n")
        print(article["text"])
        raise SystemExit
    from config import Settings
    from loaders.structured_loader import StructuredLoader
    from loaders.vector_loader import VectorLoader
    from processors.classifier import ContentClassifier
    from processors.principle_extractor import PrincipleExtractor

    settings = Settings()
    components = {
        "settings": settings,
        "structured_loader": StructuredLoader(settings),
        "vector_loader": VectorLoader(settings),
        "classifier": ContentClassifier(settings),
        "principle_extractor": PrincipleExtractor(settings),
        "contextualize": True,
        "context_model": None,
        "quarantine": True,
    }
    stats, ok = w.ingest_article(article, components, {"articles_ingested": 0, "chunks_total": 0, "principles_total": 0})
    print("ok" if ok else "FAILED", stats)
    if ok:
        done = w.load_progress(w.URL_LIST_PROGRESS_FILE)
        done.add(URL)
        w.save_progress(done, w.URL_LIST_PROGRESS_FILE)
