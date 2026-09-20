# shared/llm.py
"""
LLM client initialization. Single Anthropic client shared across agent steps.
"""

import json
import logging
import re
import time

from anthropic import Anthropic

logger = logging.getLogger(__name__)


def parse_llm_json(raw_text: str):
    """Parse JSON from an LLM response, stripping markdown code fences.

    Consolidates three hand-rolled fence-strippers (pipeline, classifier,
    principle_extractor) — the classifier's used `lstrip("json")`, a character-set
    strip that only worked because JSON starts with `{` (I-L8). Raises
    json.JSONDecodeError on unparseable input so callers keep their own handling.
    """
    text = raw_text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return json.loads(text.strip())

# ── Model capabilities & pricing ──────────────────────────────────────────
# USD per million tokens (input, output), list price as of 2026-09. Keys are
# id prefixes so dated snapshots (claude-haiku-4-5-20251001) resolve by
# longest-prefix match. Unknown ids fall back to DEFAULT_PRICING_PER_MTOK with
# a single warning — cost tracking must never crash a run.
MODEL_PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-fable-5-1": (10.0, 50.0),
    # Open models via OpenRouter (cheapest backend, 2026-09-20; MODEL-2 candidates)
    "z-ai/glm-5.3-flash": (0.09, 0.30),
    "deepseek/deepseek-v4.1-flash": (0.15, 0.60),
    "qwen/qwen3.8-flash": (0.15, 0.47),
    "moonshotai/kimi-k3": (1.70, 8.50),
}
DEFAULT_PRICING_PER_MTOK = MODEL_PRICING_PER_MTOK["claude-sonnet-4-6"]
# Prompt-cache multipliers on the input rate (5-minute ephemeral cache).
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25

# Families that reject non-default `temperature` / `top_p` / `top_k` with a 400:
# Opus 4.7+, Opus 5, Sonnet 5 and the Fable/Mythos tier. Sonnet 4.6, Opus 4.6
# and Haiku 4.5 still accept one of them.
_NO_SAMPLING_PARAMS_PREFIXES = (
    "claude-sonnet-5", "claude-opus-4-7", "claude-opus-4-8", "claude-opus-5",
    "claude-fable-", "claude-mythos-",
)
# Families where omitting `thinking` runs adaptive thinking (default-on) …
_THINKING_DEFAULT_ON_PREFIXES = ("claude-sonnet-5", "claude-opus-5", "claude-fable-", "claude-mythos-")
_THINKING_ALWAYS_ON_PREFIXES = ("claude-fable-", "claude-mythos-")
# … and the wider set that understands `thinking={"type": "adaptive"}` and
# `output_config={"effort": …}` at all (4.6 and later; Haiku 4.5 takes neither).
_ADAPTIVE_THINKING_PREFIXES = _THINKING_DEFAULT_ON_PREFIXES + (
    "claude-sonnet-4-6", "claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8",
)
# Open models reached through OpenRouter's Anthropic-compatible endpoint
# (MODEL-2). OpenRouter translates `thinking` / `output_config.effort` for
# them; these reason by default and GLM-5.3-Flash 400s on `disabled`
# ("Reasoning is mandatory for this endpoint") — "disabled" there becomes
# adaptive at low effort (43 output tokens vs 1,208 unconstrained on the probe).
_REASONING_MANDATORY_PREFIXES = ("z-ai/glm-",)
THINKING_MODES = ("", "adaptive", "disabled")
EFFORT_LEVELS = ("", "low", "medium", "high", "xhigh", "max")
_NON_TEXT_BLOCK_TYPES = frozenset({
    "thinking", "redacted_thinking", "tool_use", "server_tool_use", "fallback",
    "web_search_tool_result", "web_fetch_tool_result",
})
_warned_unknown_pricing: set[str] = set()

# ── Providers ─────────────────────────────────────────────────────────────
# "anthropic" (default) talks to api.anthropic.com with ANTHROPIC_API_KEY.
# "openrouter" talks to OpenRouter's Anthropic-Messages-compatible endpoint
# with OPENROUTER_API_KEY and OpenRouter's model ids ("anthropic/claude-sonnet-5",
# "anthropic/claude-haiku-4.5"); same SDK, same request shape. No Message
# Batches there — `supports_batches()` is False and --batch falls back to sync.
PROVIDERS = ("anthropic", "openrouter")
OPENROUTER_BASE_URL = "https://openrouter.ai/api"
_OPENROUTER_VENDOR = "anthropic/"
_DATE_SUFFIX_RE = re.compile(r"-\d{8}$")
_MINOR_VERSION_RE = re.compile(r"^(claude-[a-z]+-\d)-(\d)$")


def openrouter_model_id(model: str) -> str:
    """Anthropic id → OpenRouter id: `anthropic/` prefix, dotted minor version,
    no dated snapshot (`claude-haiku-4-5-20251001` → `anthropic/claude-haiku-4.5`).
    Ids that already carry a vendor prefix pass through."""
    if "/" in model:
        return model
    base = _DATE_SUFFIX_RE.sub("", model)
    base = _MINOR_VERSION_RE.sub(r"\1.\2", base)
    return _OPENROUTER_VENDOR + base


def canonical_model(model: str | None) -> str:
    """The Anthropic-style id behind any provider's id, for the family and
    pricing tables: strips a `vendor/` prefix and turns a dotted minor version
    back into dashes (`anthropic/claude-sonnet-4.6` → `claude-sonnet-4-6`)."""
    if not model:
        return ""
    base = model.rsplit("/", 1)[-1]
    return re.sub(r"^(claude-[a-z]+-\d)\.(\d)", r"\1-\2", base)


def supports_batches(settings) -> bool:
    """Message Batches exist on the first-party API only."""
    return getattr(settings, "llm_provider", "anthropic") in ("", "anthropic")


def _has_prefix(model: str | None, prefixes: tuple[str, ...]) -> bool:
    return bool(model) and canonical_model(model).startswith(prefixes)


def _is_open_model(model: str | None) -> bool:
    """A vendor-prefixed non-Claude id (`z-ai/glm-5.3-flash`) — reached via OpenRouter."""
    return bool(model) and "/" in model and not model.startswith(_OPENROUTER_VENDOR)


def pricing_for(model: str | None) -> tuple[float, float]:
    """(input, output) USD per million tokens for a model id (longest-prefix match)."""
    if model:
        canon = model if _is_open_model(model) else canonical_model(model)
        key = max((k for k in MODEL_PRICING_PER_MTOK if canon.startswith(k)), key=len, default=None)
        if key:
            return MODEL_PRICING_PER_MTOK[key]
        if model not in _warned_unknown_pricing:
            _warned_unknown_pricing.add(model)
            logger.warning(f"No pricing entry for model {model!r}; estimating at Sonnet 4.6 rates")
    return DEFAULT_PRICING_PER_MTOK


def accepts_sampling_params(model: str | None) -> bool:
    """False for models that 400 on a non-default temperature/top_p/top_k."""
    return not _has_prefix(model, _NO_SAMPLING_PARAMS_PREFIXES)


def sampling_kwargs(model: str | None, temperature: float | None) -> dict:
    """`{"temperature": t}` where the model accepts it, `{}` otherwise.

    Sonnet 5 / Opus 4.7+ / Opus 5 / Fable reject the parameter outright, so a
    Sonnet-4.6-tuned setting must be dropped, not clamped, when the model id is
    switched by env. Steer those models by prompt instead.
    """
    if temperature is None or not accepts_sampling_params(model):
        return {}
    return {"temperature": temperature}


def thinking_kwargs(model: str | None, mode: str = "", effort: str = "") -> dict:
    """Thinking / effort request kwargs for a model, from two settings strings.

    mode:   ""         → model default (off on 4.x, adaptive on Sonnet 5 / Opus 5 / Fable)
            "adaptive" → `thinking={"type": "adaptive"}` on 4.6+; ignored on older models
                         (they need the retired budget_tokens form)
            "disabled" → `thinking={"type": "disabled"}` on default-on models only; 4.x
                         is already off when the field is omitted, and Fable can't be
                         turned off — both are silently no-ops
    effort: "" | low | medium | high | xhigh | max → `output_config={"effort": …}` on 4.6+
    Raises ValueError on an unknown value, or on disabled + xhigh/max (a 400 on Opus 5).
    """
    mode = (mode or "").strip().lower()
    effort = (effort or "").strip().lower()
    if mode not in THINKING_MODES:
        raise ValueError(f"thinking mode must be one of {THINKING_MODES}, got {mode!r}")
    if effort not in EFFORT_LEVELS:
        raise ValueError(f"effort must be one of {EFFORT_LEVELS}, got {effort!r}")
    if mode == "disabled" and effort in ("xhigh", "max"):
        raise ValueError("thinking 'disabled' cannot be combined with effort 'xhigh'/'max'")
    kwargs: dict = {}
    if _is_open_model(model):
        if mode == "disabled" and model.startswith(_REASONING_MANDATORY_PREFIXES):
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"] = {"effort": effort or "low"}
        elif mode:
            kwargs["thinking"] = {"type": mode}
            if effort:
                kwargs["output_config"] = {"effort": effort}
        return kwargs
    if not _has_prefix(model, _ADAPTIVE_THINKING_PREFIXES):
        return kwargs
    if mode == "adaptive":
        kwargs["thinking"] = {"type": "adaptive"}
    elif (mode == "disabled" and _has_prefix(model, _THINKING_DEFAULT_ON_PREFIXES)
            and not _has_prefix(model, _THINKING_ALWAYS_ON_PREFIXES)):
        kwargs["thinking"] = {"type": "disabled"}
    if effort:
        kwargs["output_config"] = {"effort": effort}
    return kwargs


def json_schema_kwargs(schema: dict, base: dict | None = None) -> dict:
    """`base` kwargs plus an `output_config.format` constraining the reply to
    `schema` (structured outputs, STRUCT-1).

    `output_config` also carries `effort` (from `thinking_kwargs`), so pass those
    kwargs as `base` and this merges into them — `{**thinking_kwargs(...),
    **json_schema_kwargs(schema)}` would silently drop one of the two.

    The reply is still a text block holding JSON, so `message_text` + the
    existing parsers keep working — the schema just guarantees it parses and
    conforms, which retires the fence/regex salvage paths. Schema rules the API
    enforces (400 otherwise): every object carries `additionalProperties: false`,
    no `minimum`/`maximum`/`minLength`/`pattern`, arrays only `minItems` 0|1,
    enums of scalars only; optional properties are fine (leave them out of
    `required`). Supported on every model in MODEL_PRICING_PER_MTOK. Works with
    thinking (the JSON follows the thinking block), Message Batches and prompt
    caching; changing the schema invalidates the prompt cache for that request
    shape, so keep a schema constant across a program's sessions.
    """
    out = dict(base or {})
    out["output_config"] = {
        **(out.get("output_config") or {}),
        "format": {"type": "json_schema", "schema": schema},
    }
    return out


class LLMRefusal(RuntimeError):
    """The model's safety classifiers declined the request (stop_reason='refusal')."""


def message_text(response) -> str:
    """Concatenate the text blocks of a Messages response.

    Claude 5 models run adaptive thinking by default, so `content[0]` can be a
    thinking block rather than the answer; indexing it broke every caller that
    read `response.content[0].text`. Skips thinking / tool blocks, and raises
    LLMRefusal when the response is an empty refusal so callers surface it
    instead of trying to parse "".
    """
    parts = []
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", "text") in _NON_TEXT_BLOCK_TYPES:
            continue
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    text = "".join(parts)
    if not text and getattr(response, "stop_reason", None) == "refusal":
        raise LLMRefusal("model declined the request (stop_reason='refusal')")
    return text


def usage_tokens(usage) -> dict[str, int]:
    """input / output / cache_read / cache_creation token counts from a usage object.

    Missing or non-int fields (older SDKs, mocks) read as 0. With prompt caching,
    `input_tokens` counts only the *uncached* prefix — the cached part is billed
    separately, which is why cost needs all four numbers.
    """
    out = {}
    for key, attr in (("input", "input_tokens"), ("output", "output_tokens"),
                      ("cache_read", "cache_read_input_tokens"),
                      ("cache_creation", "cache_creation_input_tokens")):
        value = getattr(usage, attr, 0)
        out[key] = value if isinstance(value, int) and not isinstance(value, bool) else 0
    return out

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


def create_message_growing(client, *, max_tokens: int, ceiling: int | None = None,
                           label: str = "LLM call", **kwargs):
    """`create_message_with_retries` that re-sends with a doubled `max_tokens`
    (up to `ceiling`, default LLM_MAX_TOKENS_CEILING) whenever the response
    stops on `max_tokens`. A truncated JSON payload (a program template, a
    principle list) is worthless, and on Sonnet 5 / Opus 5 adaptive thinking
    can spend the whole budget before any text — so re-sending the same
    request never helps; a bigger budget does. Returns the final response.
    """
    from shared.constants import LLM_MAX_TOKENS_CEILING

    ceiling = ceiling or LLM_MAX_TOKENS_CEILING
    budget = max_tokens
    while True:
        response = create_message_with_retries(client, max_tokens=budget, **kwargs)
        if getattr(response, "stop_reason", None) != "max_tokens" or budget >= ceiling:
            return response
        grown = min(budget * 2, ceiling)
        logger.warning(f"{label}: response truncated at max_tokens={budget} — retrying with {grown}")
        budget = grown


# The API's constraint on a batch request's custom_id (a colon in "3:0" 400'd
# the first --batch principle run on 2026-09-20, after the OCR batch had run).
_BATCH_CUSTOM_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


class BatchRequestFailed(RuntimeError):
    """One request in a Message Batch did not succeed (errored / expired / canceled)."""

    def __init__(self, custom_id: str, result_type: str, detail: str = ""):
        self.custom_id = custom_id
        self.result_type = result_type
        super().__init__(f"batch request {custom_id!r} {result_type}{': ' + detail if detail else ''}")


def _batch_error_detail(result) -> str:
    error = getattr(result, "error", None)
    inner = getattr(error, "error", None) or error
    message = getattr(inner, "message", None)
    if message:
        return str(message)
    return str(error) if error else ""


def run_message_batch(client, requests: dict[str, dict], *, label: str = "batch",
                      poll_interval: float | None = None, timeout: float | None = None,
                      grow: bool = True, ceiling: int | None = None) -> dict[str, object]:
    """Send `requests` ({custom_id: messages.create kwargs}) through the Message
    Batches API and return {custom_id: Message | BatchRequestFailed}.

    Half the price of synchronous calls (COST-1) for work that can wait — the
    ingestion pipeline's principle extraction, template parsing, OCR and
    relabelling. Submits in slices of BATCH_MAX_REQUESTS, polls each until it
    has ended, then collects results. With `grow`, requests whose reply stopped
    on `max_tokens` are re-sent in a follow-up batch with a doubled budget, up
    to `ceiling` (default LLM_MAX_TOKENS_CEILING) — the batch counterpart of
    `create_message_growing`. Requests that error, expire or are canceled come
    back as BatchRequestFailed so the caller can decide per item; a batch that
    never ends within `timeout` raises TimeoutError.
    """
    from shared.constants import (
        BATCH_MAX_REQUESTS,
        BATCH_POLL_INTERVAL_S,
        BATCH_TIMEOUT_S,
        LLM_MAX_TOKENS_CEILING,
    )

    poll_interval = BATCH_POLL_INTERVAL_S if poll_interval is None else poll_interval
    timeout = BATCH_TIMEOUT_S if timeout is None else timeout
    ceiling = ceiling or LLM_MAX_TOKENS_CEILING

    bad = [cid for cid in requests if not _BATCH_CUSTOM_ID_RE.fullmatch(cid)]
    if bad:
        raise ValueError(
            f"{label}: custom_id must match {_BATCH_CUSTOM_ID_RE.pattern!r}; got {bad[:3]!r}"
        )

    results: dict[str, object] = {}
    pending = dict(requests)
    round_no = 0
    while pending:
        round_no += 1
        ids = list(pending)
        for start in range(0, len(ids), BATCH_MAX_REQUESTS):
            slice_ids = ids[start:start + BATCH_MAX_REQUESTS]
            batch = client.messages.batches.create(
                requests=[{"custom_id": cid, "params": pending[cid]} for cid in slice_ids]
            )
            logger.info(
                f"{label}: submitted batch {batch.id} ({len(slice_ids)} requests"
                f"{f', round {round_no}' if round_no > 1 else ''})"
            )
            batch = _wait_for_batch(client, batch, label, poll_interval, timeout)
            for item in client.messages.batches.results(batch.id):
                result = item.result
                kind = getattr(result, "type", "errored")
                if kind == "succeeded":
                    results[item.custom_id] = result.message
                else:
                    results[item.custom_id] = BatchRequestFailed(
                        item.custom_id, kind, _batch_error_detail(result)
                    )
            missing = [cid for cid in slice_ids if cid not in results]
            for cid in missing:  # a result the API never returned — treat as errored
                results[cid] = BatchRequestFailed(cid, "missing")

        if not grow:
            break
        regrow: dict[str, dict] = {}
        for cid in ids:
            message = results.get(cid)
            if getattr(message, "stop_reason", None) != "max_tokens":
                continue
            budget = pending[cid].get("max_tokens", 0)
            if budget >= ceiling:
                continue
            regrow[cid] = {**pending[cid], "max_tokens": min(budget * 2, ceiling)}
        if regrow:
            logger.warning(
                f"{label}: {len(regrow)} response(s) truncated at max_tokens — "
                f"re-sending with a doubled budget"
            )
        pending = regrow
    return results


def _wait_for_batch(client, batch, label: str, poll_interval: float, timeout: float):
    """Poll a Message Batch until `processing_status == 'ended'`."""
    deadline = time.monotonic() + timeout
    while getattr(batch, "processing_status", None) != "ended":
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{label}: batch {batch.id} did not end within {timeout:.0f}s")
        time.sleep(poll_interval)
        batch = client.messages.batches.retrieve(batch.id)
        counts = getattr(batch, "request_counts", None)
        if counts is not None:
            logger.info(
                f"{label}: batch {batch.id} {batch.processing_status} — "
                f"{getattr(counts, 'succeeded', 0)} ok / {getattr(counts, 'errored', 0)} err / "
                f"{getattr(counts, 'processing', 0)} pending"
            )
    return batch


def create_llm_client(settings) -> Anthropic:
    """The one place an Anthropic-SDK client is built (agent, ingestion, eval).

    `settings.llm_provider` picks the endpoint: the first-party API with
    ANTHROPIC_API_KEY, or OpenRouter's Anthropic-compatible endpoint with
    OPENROUTER_API_KEY (Settings has already rewritten the model ids).
    """
    provider = getattr(settings, "llm_provider", "") or "anthropic"
    if provider == "openrouter":
        if not getattr(settings, "openrouter_api_key", ""):
            raise ValueError("OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter. Set it in .env.")
        return Anthropic(
            api_key=settings.openrouter_api_key,
            base_url=getattr(settings, "llm_base_url", "") or OPENROUTER_BASE_URL,
        )
    if provider != "anthropic":
        raise ValueError(f"LLM_PROVIDER must be one of {PROVIDERS}, got {provider!r}")
    if not settings.anthropic_api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is required. Set it in .env or as an environment variable."
        )
    return Anthropic(api_key=settings.anthropic_api_key)


def estimate_cost(
    input_tokens: int,
    output_tokens: int,
    model: str | None = None,
    *,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Estimate USD cost for a single LLM call at the model's list price.

    `model=None` prices at Sonnet 4.6 rates (the pre-2026-09 behaviour, when the
    rate was a module constant). Pass the cache token counts too when the call
    used prompt caching — `input_tokens` alone excludes the cached prefix.
    """
    in_rate, out_rate = pricing_for(model)
    return (
        input_tokens * in_rate
        + cache_read_tokens * in_rate * CACHE_READ_MULTIPLIER
        + cache_creation_tokens * in_rate * CACHE_WRITE_MULTIPLIER
        + output_tokens * out_rate
    ) / 1_000_000


def light_model_for(settings, explicit: str | None = None) -> str:
    """The model for a cheap label/summary task: an explicit CLI/API choice wins,
    then ``settings.light_model``, then ``settings.llm_model`` (for callers built
    with a partial settings object)."""
    if explicit:
        return explicit
    light = getattr(settings, "light_model", None)
    return light if isinstance(light, str) and light else settings.llm_model
