# shared/llm.py
"""
LLM client initialization. Single Anthropic client shared across agent steps.
"""

from anthropic import Anthropic

# Token cost estimates (Claude Sonnet 4, as of 2025)
COST_PER_INPUT_TOKEN = 3.0 / 1_000_000    # $3.00 per 1M input tokens
COST_PER_OUTPUT_TOKEN = 15.0 / 1_000_000  # $15.00 per 1M output tokens


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
