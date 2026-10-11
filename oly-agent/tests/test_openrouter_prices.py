"""eval.openrouter_prices — per-provider price rows and the sort=price pick (no network)."""
from eval.openrouter_prices import configured_models, parse_endpoints, render

from shared.config import Settings


def _ep(provider, inp, out, cache=None, schema=True, status=0, quant="fp4"):
    params = ["max_tokens"] + (["structured_outputs"] if schema else [])
    pricing = {"prompt": str(inp / 1e6), "completion": str(out / 1e6)}
    if cache is not None:
        pricing["input_cache_read"] = str(cache / 1e6)
    return {"provider_name": provider, "tag": provider.lower(), "quantization": quant, "pricing": pricing,
            "supported_parameters": params, "status": status, "uptime_last_30m": 100}


def test_rows_sorted_by_call_cost_and_pick_skips_unhealthy_and_schemaless():
    payload = {"data": {"endpoints": [
        _ep("Pricey", 3.0, 15.0, 0.3),
        _ep("NoSchema", 0.1, 0.1, schema=False),
        _ep("Down", 0.2, 0.2, status=-5),
        _ep("Cheap", 0.8, 13.5, 0.55),
    ]}}
    rows = parse_endpoints("moonshotai/kimi-k3", payload, mix=(1_000_000, 0, 0))
    assert [r.provider for r in rows] == ["NoSchema", "Down", "Cheap", "Pricey"]
    assert [r.provider for r in rows if r.pick] == ["Cheap"]
    assert rows[2].cost_per_call == 0.8


def test_cache_read_priced_at_input_rate_when_endpoint_has_none():
    payload = {"data": {"endpoints": [_ep("A", 2.0, 10.0), _ep("B", 2.0, 10.0, cache=0.1)]}}
    by = {r.provider: r for r in parse_endpoints("m/x", payload, mix=(0, 1_000_000, 0))}
    assert by["A"].cost_per_call == 2.0 and by["B"].cost_per_call == 0.1
    assert by["A"].cache_read_per_mtok is None
    assert "m/x" in render(list(by.values()), (0, 1_000_000, 0))


def test_configured_models_are_the_openrouter_roles_deduped():
    models = configured_models(Settings(llm_provider="openrouter", openrouter_api_key="k"))
    assert models == ["openai/gpt-6.1-sol", "moonshotai/kimi-k3", "deepseek/deepseek-v4.1-flash", "z-ai/glm-5.3-flash"]
