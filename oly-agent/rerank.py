# oly-agent/rerank.py
"""
Optional listwise rerank of retrieval candidates by the light model (AUD-3).

One LLM call per query: the top RERANK_TOP_N candidates are shown as numbered
passages (a query-focused excerpt of each, shared/excerpt.focused_excerpt) and
the model returns the passage numbers best-first under a JSON schema. The
result is cached per (model, query, candidate ids), thread-safe. Any failure —
API error, refusal, malformed or empty ranking — returns the candidates in
their original order and logs a warning; the rerank can never lose a chunk:
ids the model leaves out keep their original relative order after the ranked
ones.

Behind RERANK_ENABLED (shared/constants.py, default off — see
docs/RETRIEVAL_EVAL.md for the measurement that decided it).
"""

import json
import logging
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.constants import RERANK_MAX_ATTEMPTS, RERANK_MAX_TOKENS, RERANK_SNIPPET_CHARS, RERANK_TOP_N
from shared.excerpt import focused_excerpt
from shared.llm import (
    create_message_with_retries,
    estimate_cost,
    json_schema_kwargs,
    light_model_for,
    message_text,
    thinking_kwargs,
    usage_tokens,
)

logger = logging.getLogger(__name__)

RERANK_SCHEMA = {
    "type": "object",
    "properties": {
        "ranking": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["ranking"],
    "additionalProperties": False,
}

RERANK_PROMPT = """You rank reference passages for an Olympic weightlifting coach.

The coach's retrieval query:
{query}

Passages:
{passages}

Rank the passages by how directly useful each one is for answering the query —
concrete programming guidance for this situation (exercise choice, sets, reps,
intensity, phase, fault correction) beats general background; off-topic text
goes last. Return JSON {{"ranking": [passage numbers, most useful first]}},
listing every passage number exactly once."""


def _passage_text(chunk: dict, query: str, max_chars: int) -> str:
    text = chunk.get("raw_content") or chunk.get("content") or ""
    return " ".join(focused_excerpt(text, query, max_chars).split())


def parse_ranking(text: str, n: int) -> list[int]:
    """0-based candidate indices from the model's reply, deduped, in range.

    The schema asks for 1-based passage numbers. Raises ValueError when the
    reply is not the expected JSON or names no valid passage."""
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("ranking"), list):
        raise ValueError(f"reply has no 'ranking' list: {text[:200]!r}")
    order: list[int] = []
    for v in data["ranking"]:
        if isinstance(v, bool) or not isinstance(v, int):
            continue
        idx = v - 1
        if 0 <= idx < n and idx not in order:
            order.append(idx)
    if not order:
        raise ValueError(f"ranking names no valid passage (1..{n}): {text[:200]!r}")
    return order


class ListwiseReranker:
    """Rerank candidate chunks for a query with one schema-constrained LLM call.

    Thread-safe: the cache and the spend counters sit behind one lock; the LLM
    call itself runs outside it (two threads with the same uncached query may
    both call — harmless, the second write wins with an identical order)."""

    def __init__(self, client, model: str, top_n: int = RERANK_TOP_N,
                 snippet_chars: int = RERANK_SNIPPET_CHARS, max_tokens: int = RERANK_MAX_TOKENS):
        self.client = client
        self.model = model
        self.top_n = top_n
        self.snippet_chars = snippet_chars
        self.max_tokens = max_tokens
        self._cache: dict[tuple, list[int]] = {}
        self._lock = threading.Lock()
        self.calls = 0
        self.failures = 0
        self.cost_usd = 0.0

    @classmethod
    def from_settings(cls, settings, model: str | None = None, **kwargs) -> "ListwiseReranker":
        from shared.llm import create_llm_client

        return cls(create_llm_client(settings), light_model_for(settings, model), **kwargs)

    def _request(self, query: str, candidates: list[dict]) -> dict:
        passages = "\n\n".join(
            f"[{i}] {_passage_text(c, query, self.snippet_chars)}" for i, c in enumerate(candidates, 1)
        )
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": RERANK_PROMPT.format(query=query, passages=passages)}],
            **json_schema_kwargs(RERANK_SCHEMA, thinking_kwargs(self.model, "disabled")),
        }

    def rerank(self, query: str, candidates: list[dict], top_k: int | None = None) -> list[dict]:
        """`candidates` best-first; returns them reranked (the first top_n
        reordered, the rest after them unchanged), cut to `top_k` when given.

        Each reranked row is a copy carrying `rerank_score` (1.0 for the first,
        falling linearly), which `retrieve._rank` sorts by ahead of `score`."""
        if len(candidates) < 2:
            return list(candidates)[:top_k] if top_k else list(candidates)
        head, tail = list(candidates[: self.top_n]), list(candidates[self.top_n:])
        key = (self.model, query, tuple(c.get("id") for c in head))
        with self._lock:
            order = self._cache.get(key)
        if order is None:
            try:
                resp = create_message_with_retries(
                    self.client, max_attempts=RERANK_MAX_ATTEMPTS, **self._request(query, head)
                )
                order = parse_ranking(message_text(resp), len(head))
                tok = usage_tokens(getattr(resp, "usage", None))
                cost = estimate_cost(tok["input"], tok["output"], self.model,
                                     cache_read_tokens=tok["cache_read"],
                                     cache_creation_tokens=tok["cache_creation"])
                with self._lock:
                    self._cache[key] = order
                    self.calls += 1
                    self.cost_usd += cost
            except Exception as e:  # any failure → the retrieval order
                with self._lock:
                    self.failures += 1
                logger.warning(f"Rerank failed for query {query[:60]!r} ({type(e).__name__}: {e}); "
                               "keeping the retrieval order")
                out = list(candidates)
                return out[:top_k] if top_k else out
        ranked = order + [i for i in range(len(head)) if i not in order]
        n = len(head)
        out = [{**head[i], "rerank_score": round(1.0 - pos / n, 4)} for pos, i in enumerate(ranked)] + tail
        return out[:top_k] if top_k else out
