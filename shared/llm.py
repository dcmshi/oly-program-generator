# shared/llm.py
"""
LLM client initialization. Single Anthropic client shared across agent steps.
"""

import logging
import time

from anthropic import Anthropic

logger = logging.getLogger(__name__)

# Token cost estimates (Claude Sonnet 4, as of 2025)
COST_PER_INPUT_TOKEN = 3.0 / 1_000_000    # $3.00 per 1M input tokens
COST_PER_OUTPUT_TOKEN = 15.0 / 1_000_000  # $15.00 per 1M output tokens

# HTTP statuses worth retrying: rate limit, server errors, Anthropic overloaded
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 529})


def create_message_with_retries(client, *, max_attempts: int = 3, base_delay: float = 2.0, **kwargs):
    """client.messages.create with exponential backoff on transient errors.

    Retries connection errors, timeouts, rate limits, and 5xx/overloaded
    responses. Non-retryable API errors (auth, bad request) raise immediately,
    as does the last error once attempts are exhausted.
    """
    from anthropic import APIConnectionError, APIStatusError

    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            return client.messages.create(**kwargs)
        except APIConnectionError as e:  # includes APITimeoutError
            last_exc = e
        except APIStatusError as e:  # includes RateLimitError (429), 5xx, 529
            if e.status_code not in RETRYABLE_STATUS_CODES:
                raise
            last_exc = e
        if attempt < max_attempts:
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                f"Anthropic call failed ({type(last_exc).__name__}: {last_exc}); "
                f"retrying in {delay:.0f}s (attempt {attempt}/{max_attempts})"
            )
            time.sleep(delay)
    raise last_exc


def create_llm_client(settings) -> Anthropic:
    """Create the Anthropic client.

    Used by:
    - generate.py (Step 4: per-session program generation)
    - explain.py  (Step 6: program rationale)
    - plan.py     (optional, for ambiguous planning decisions)
    """
    if not settings.anthropic_api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is required. Set it in .env or as an environment variable."
        )
    return Anthropic(api_key=settings.anthropic_api_key)


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for a single LLM call."""
    return (
        input_tokens * COST_PER_INPUT_TOKEN
        + output_tokens * COST_PER_OUTPUT_TOKEN
    )
