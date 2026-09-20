# processors/jev_judge.py
"""
Label / score decisions through Jev, TypeSafe AI's System One model.

Jev is not an LLM: it takes *state* (a passage) plus typed questions and returns
calibrated probabilities and a confidence in ~100-500 ms at $0.042 per million
input tokens with free output. That is exactly the shape of the corpus's
judgement calls — pick a chunk_type with a confidence — so those can run on it
instead of a Haiku-class model (~$0.08 for the whole corpus vs ~$3).

Only decisions live here; anything that generates text (principle extraction,
template parsing, OCR, contextualize, the agent) stays on the LLM roles.
Needs TYPESAFE_API_KEY (the SDK reads it from the environment; shared/config.py
loads .env). Import lazily — the SDK is an ingestion dependency only.
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # repo root for shared.*
from shared.schema_enums import CHUNK_TYPES

logger = logging.getLogger(__name__)

# One line per chunk_type, the same wording relabel_chunk_types.RELABEL_PROMPT
# gives the LLM judge, so the two judges answer the same question.
CHUNK_TYPE_CRITERIA: dict[str, str] = {
    "periodization": "how training is organised over time — phases, blocks, cycles, accumulation/intensification/realization, deloads, tapering, peaking, annual plans",
    "programming_rationale": "WHY a prescription is made — exercise selection reasoning, volume/intensity trade-offs, how a coach decides what goes in a session",
    "methodology": "a named training method or system described as a whole (e.g. Bulgarian, Soviet, conjugate) and how it is applied",
    "fault_correction": "diagnosing and fixing technical errors in the lifts — missed positions, bar path faults, drills to correct them",
    "biomechanics": "anatomy, physics and mechanics of the lifts — positions, forces, bar path analysis, muscle involvement",
    "recovery_adaptation": "fatigue, supercompensation, sleep, restoration, overtraining, how the body adapts to load",
    "competition_strategy": "meet day — attempt selection, openers, warm-up room timing, weigh-in tactics",
    "nutrition_bodyweight": "diet, making weight, weight classes, hydration, body composition",
    "case_study": "a specific athlete's or team's training history told as an example",
    "concept": "general explanation or history that fits none of the above",
}
assert set(CHUNK_TYPE_CRITERIA) == set(CHUNK_TYPES)

CHUNK_TYPE_INSTRUCTIONS = (
    "Label this passage from Olympic weightlifting coaching literature with exactly ONE content type."
)
DEFAULT_CONCURRENCY = 8


async def label_chunk_types_async(
    passages: dict[int, str], *, concurrency: int = DEFAULT_CONCURRENCY, model: str | None = None,
) -> dict[int, tuple[str, float]]:
    """{index: passage} → {index: (chunk_type, confidence)} — one Jev call per
    passage, `concurrency` in flight. A passage whose call fails is logged and
    left out, so the caller keeps its current label (as with a skipped LLM batch)."""
    from typesafe_sdk import AsyncTypeSafeClient, Choice

    question = Choice(instructions=CHUNK_TYPE_INSTRUCTIONS, criteria=CHUNK_TYPE_CRITERIA)
    out: dict[int, tuple[str, float]] = {}
    sem = asyncio.Semaphore(concurrency)

    async with AsyncTypeSafeClient(model=model) as client:
        async def one(idx: int, text: str) -> None:
            async with sem:
                try:
                    response = await client.system_one(state={"passage": text}, questions={"chunk_type": question})
                except Exception as e:
                    logger.warning(f"Jev chunk_type failed for passage {idx}: {type(e).__name__}: {e}")
                    return
            answer = response.choices["chunk_type"]
            out[idx] = (str(answer.choice), float(answer.confidence))

        await asyncio.gather(*(one(i, t) for i, t in passages.items()))
    return out


def label_chunk_types(passages: dict[int, str], **kwargs) -> dict[int, tuple[str, float]]:
    """Synchronous wrapper around `label_chunk_types_async`."""
    return asyncio.run(label_chunk_types_async(passages, **kwargs))
