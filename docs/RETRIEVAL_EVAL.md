# Retrieval Quality Tracker

Run: `cd oly-ingestion && PYTHONUTF8=1 uv run python tests/test_retrieval_eval.py`

## Baseline — 2026-03-18

Corpus: 3,796 chunks · 151 principles · 11 sources (incl. Takano, source_id=2)
Settings: `top_k=5`, `min_similarity=0.45`

| Q# | Query (abbreviated) | Sim range | Topic hits | Type hits | Notes |
|----|---------------------|-----------|------------|-----------|-------|
| 1 | Accumulation volume structure | 0.472–0.511 | ✅ | ✅ | Top result still Soviet abbreviation noise (`concept` type); blocked in agent by chunk_type filter. |
| 2 | Snatch misses forward — exercises | 0.681–0.694 | ✅ | ✅ | |
| 3 | Weeks out for volume reduction | 0.482–0.590 | ✅ | ✅ | 3 results (2 filtered by threshold). Chunks correctly `concept` type. |
| 4 | Beginner → structured programming | — | n/a | n/a | 0 results after threshold — corpus lacks strong content here. Honest. |
| 5 | Squat strength vs C&J | 0.611–0.679 | ✅ | ✅ | |
| 6 | Jerk press-out cause & fix | 0.653–0.680 | ✅ | ✅ | |
| 7 | Clean catch + thoracic mobility | 0.553–0.583 | ✅ | ✅ | |
| 8 | Recovery between heavy sessions | 0.643–0.698 | ✅ | ✅ | Chunks correctly `recovery_adaptation` type. |
| 9 | Competition attempt selection | 0.602–0.655 | ✅ | ✅ | |
| 10 | Multi-year training structure | 0.619–0.664 | ✅ | ✅ | |
| 11 | Sessions/week for intermediate | 0.601–0.621 | ✅ | ✅ | |
| 12 | Prilepin 80–90% sets/reps | 0.461–0.497 | ✅ | ✅ | Low sim expected — Prilepin lives in structured table, not vector store. |
| 13 | Upper back hypertrophy volume | 0.539–0.638 | ✅ | ✅ | Top result now Catalyst back-training article (improved after threshold). Content gap documented in O2. |
| 14 | Hypertrophy alongside comp lifts | 0.580–0.640 | ✅ | ✅ | |
| 15 | Hip mobility for clean receive | 0.588–0.607 | ✅ | ✅ | |
| 16 | Overhead mobility for snatch | 0.700–0.714 | ✅ | ✅ | |
| 17 | Ankle mobility → squat fold | 0.568–0.602 | ✅ | ✅ | |
| 18 | Off-season GPP | 0.605–0.631 | ✅ | ✅ | |
| 19 | Loaded carries + conditioning | 0.582–0.603 | ✅ | ✅ | |
| 20 | RPE autoregulation | 0.496–0.540 | ✅ | ✅ | |
| 21 | Barbell curl (negative) | 0.504–0.569 | n/a | n/a | Returns 5 results — all above threshold. Inherent vector search limitation (see O3). |
| 22 | Back squat percentage (ambiguous) | 0.556–0.607 | n/a | n/a | Multi-source, expected. |

## Open Issues

### O1 — Soviet abbreviation noise (Q1) — RESOLVED 2026-03-18
**Symptom**: Q1 top result was a Medvedev chunk of unexpanded abbreviations at sim=0.511.

**Resolution**: Two-layer defence implemented:
1. `VECTOR_SEARCH_MIN_SIMILARITY = 0.45` added to `shared/constants.py`; `min_similarity` param added to `vector_loader.similarity_search()` (SQL WHERE filter, doesn't count against top_k)
2. Agent's session/limiter searches already filter to `programming_rationale`/`periodization` chunk_types — Soviet `concept` chunks never reach the agent regardless of threshold

The Q1 abbreviation chunk (0.511, `concept` type) still appears in the unfiltered eval but is blocked in production by chunk_type filtering. Re-embedding with expanded abbreviations not worth the cost.

### O2 — Israelit per-muscle volume prescriptions absent (Q13) — CLOSED (content gap)
**Symptom**: No source has specific per-muscle MEV/MAV/MRV volume tables.

**Resolution**: Accepted. Israetel covers the framework; per-muscle tables don't exist in the corpus. Eval expectation updated to not require numbers. Q13 top result improved to Catalyst "Back Training for Weightlifting" (sim=0.638) after threshold filtering removed low-quality chunks. Adding a new source with muscle-specific tables (e.g. RP app data) would close the gap — low priority for a weightlifting tool.

### O3 — Q21 negative test (barbell curl) always returns results — CLOSED (inherent limitation)
**Symptom**: Vector search returns 5 results (sim 0.504–0.569) for out-of-domain queries.
**Resolution**: Expected — `similarity_search` always returns top_k. The 0.45 threshold doesn't help here since all results are above it. A binary relevance gate would be needed but adds significant complexity. In practice the agent's chunk_type filters block most irrelevant content anyway.
