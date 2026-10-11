"""Per-provider OpenRouter prices for the models we route to (MODEL-3).

OpenRouter serves one model id from many providers at very different prices —
Kimi K3 input runs $0.75–4.50 / Mtok across ~25 endpoints — and
`MODEL_PRICING_PER_MTOK` holds one number per model. This prints every
endpoint of each model with its quantization, structured-output support and
health, prices a typical call at a given token mix, and marks the endpoint
`OPENROUTER_PROVIDER_SORT=price` should land on (the cheapest healthy one that
supports structured outputs — `require_parameters` excludes the rest).

    cd oly-agent
    PYTHONUTF8=1 uv run python -m eval.openrouter_prices                 # the configured roles
    PYTHONUTF8=1 uv run python -m eval.openrouter_prices --model openai/gpt-6.1-sol --model moonshotai/kimi-k3
    PYTHONUTF8=1 uv run python -m eval.openrouter_prices --mix 4250,800,750 --json prices.json

Public endpoint, no API key, no cost. The default mix is one session-generation
call from `model_baseline_20260920T190526Z.json` (Kimi K3: 38,245 input + 7,409
cache-read + 6,594 output tokens over 9 calls). List prices are not bills:
`price` sorting weighs OpenRouter's own blend, and a provider's cache discount
only applies when the request lands there again.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root for shared.*

from shared.config import Settings  # noqa: E402

ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{model}/endpoints"
DEFAULT_MIX = (4250, 800, 750)    # uncached input, cache-read input, output tokens per call
REQUIRED_PARAMETER = "structured_outputs"


@dataclass
class Endpoint:
    model: str
    provider: str
    tag: str
    quantization: str
    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float | None
    structured_outputs: bool
    healthy: bool
    uptime_30m: float | None
    cost_per_call: float = 0.0
    pick: bool = False


def _per_mtok(value) -> float | None:
    try:
        return round(float(value) * 1e6, 4) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_endpoints(model: str, payload: dict, mix: tuple[int, int, int] = DEFAULT_MIX) -> list[Endpoint]:
    """Endpoint rows for one model's `/endpoints` payload, cheapest call first;
    the first healthy row with structured outputs is marked `pick`."""
    uncached, cached, out = mix
    rows = []
    for e in (payload.get("data") or {}).get("endpoints") or []:
        p = e.get("pricing") or {}
        inp, outp, cache = _per_mtok(p.get("prompt")) or 0.0, _per_mtok(p.get("completion")) or 0.0, _per_mtok(p.get("input_cache_read"))
        cost = (uncached * inp + cached * (cache if cache is not None else inp) + out * outp) / 1e6
        rows.append(Endpoint(
            model=model, provider=e.get("provider_name", "?"), tag=e.get("tag") or "",
            quantization=e.get("quantization") or "unknown",
            input_per_mtok=inp, output_per_mtok=outp, cache_read_per_mtok=cache,
            structured_outputs=REQUIRED_PARAMETER in (e.get("supported_parameters") or []),
            healthy=e.get("status", 0) == 0, uptime_30m=e.get("uptime_last_30m"),
            cost_per_call=round(cost, 6),
        ))
    rows.sort(key=lambda r: (r.cost_per_call, r.provider))
    pick = next((r for r in rows if r.healthy and r.structured_outputs), None)
    if pick:
        pick.pick = True
    return rows


def fetch(model: str, client: httpx.Client) -> dict:
    r = client.get(ENDPOINTS_URL.format(model=model), timeout=30)
    r.raise_for_status()
    return r.json()


def configured_models(settings: Settings) -> list[str]:
    roles = (settings.generation_model, settings.explanation_model, settings.llm_model,
             settings.light_model, settings.judge_model)
    return list(dict.fromkeys(m for m in roles if m))


def render(rows: list[Endpoint], mix: tuple[int, int, int]) -> str:
    lines = [f"Cost per call at {mix[0]:,} input + {mix[1]:,} cache-read + {mix[2]:,} output tokens. "
             "* = where sort=price + require_parameters should route.", ""]
    for model in dict.fromkeys(r.model for r in rows):
        mrows = [r for r in rows if r.model == model]
        lines.append(f"{model}  ({len(mrows)} endpoints)")
        lines.append(f"  {'':1} {'provider':<16} {'tag':<26} {'quant':<8} {'in $/M':>7} {'out $/M':>8} "
                     f"{'cache $/M':>9} {'$/call':>9} {'schema':>6} {'up 30m':>7}")
        for r in mrows:
            cache = f"{r.cache_read_per_mtok:.3f}" if r.cache_read_per_mtok is not None else "-"
            up = f"{r.uptime_30m:.0f}%" if r.uptime_30m is not None else "-"
            flag = "*" if r.pick else ("x" if not r.healthy else " ")
            lines.append(f"  {flag:1} {r.provider[:16]:<16} {r.tag[:26]:<26} {r.quantization[:8]:<8} "
                         f"{r.input_per_mtok:>7.3f} {r.output_per_mtok:>8.3f} {cache:>9} {r.cost_per_call:>9.5f} "
                         f"{'yes' if r.structured_outputs else 'no':>6} {up:>7}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", action="append", help="OpenRouter model id (repeatable; default: the configured roles)")
    ap.add_argument("--mix", default=",".join(map(str, DEFAULT_MIX)),
                    help="uncached,cache_read,output tokens per call (default: one session-generation call)")
    ap.add_argument("--json", type=Path, help="also write the rows as JSON")
    args = ap.parse_args(argv)
    mix = tuple(int(x) for x in args.mix.split(","))
    if len(mix) != 3:
        ap.error("--mix takes three integers: uncached,cache_read,output")
    models = args.model or configured_models(Settings(llm_provider="openrouter"))
    rows: list[Endpoint] = []
    with httpx.Client() as client:
        for model in models:
            if not model.count("/"):
                print(f"skipping {model!r}: not an OpenRouter id", file=sys.stderr)
                continue
            try:
                rows += parse_endpoints(model, fetch(model, client), mix)
            except httpx.HTTPError as e:
                print(f"{model}: {e}", file=sys.stderr)
    print(render(rows, mix))
    if args.json:
        args.json.write_text(json.dumps([asdict(r) for r in rows], indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
