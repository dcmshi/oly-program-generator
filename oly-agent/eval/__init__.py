# oly-agent/eval — retrieval evaluation harness (RAG-M6).
#
# metrics.py       recall@k, MRR, nDCG@k, source diversity (pure)
# queries.py       the production-SHAPED query set (built with retrieve.py's own
#                  query builders) + the legacy 22 free-form questions
# build_golden.py  candidate pool per query (dense ∪ hybrid) → LLM-graded 0/1/2
#                  → golden.json  (paid, run once per corpus change)
# run_eval.py      score the live retriever against golden.json under production
#                  settings and compare with baseline.json (exit 1 on regression)
