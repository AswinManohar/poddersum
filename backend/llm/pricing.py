"""Cost arithmetic from token counts and the prices in ``models.yaml``.

Pure functions; no LiteLLM price map involved (it may not know our models).

Token semantics (as reported by providers via LiteLLM usage):
- ``input_tokens``: all prompt tokens, including audio and cached ones.
- ``audio_input_tokens``: prompt tokens that are audio, including cached audio.
- ``cached_tokens``: prompt tokens served from the provider cache (text + audio).
- ``cached_audio_tokens``: the audio part of ``cached_tokens`` (0 if unknown).
- ``output_tokens``: completion tokens including thinking tokens.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from llm.config import ModelPrices

COST_DECIMALS = 8  # 1e-8 USD resolution
_PER = Decimal(1_000_000)
_QUANT = Decimal(1).scaleb(-COST_DECIMALS)


def _rate(price_per_1m: float) -> Decimal:
    return Decimal(str(price_per_1m)) / _PER


def compute_cost(
    prices: ModelPrices,
    *,
    input_tokens: int,
    output_tokens: int,
    audio_input_tokens: int = 0,
    cached_tokens: int = 0,
    cached_audio_tokens: int = 0,
) -> float:
    """Return the USD cost of one call, rounded half-up to ``COST_DECIMALS`` places."""
    counts = (input_tokens, output_tokens, audio_input_tokens, cached_tokens, cached_audio_tokens)
    if any(c < 0 for c in counts):
        raise ValueError("token counts must be non-negative")

    audio_total = min(audio_input_tokens, input_tokens)
    cached_audio = min(cached_audio_tokens, audio_total, cached_tokens)
    cached_text = min(cached_tokens - cached_audio, input_tokens - audio_total)
    fresh_audio = audio_total - cached_audio
    fresh_text = input_tokens - audio_total - cached_text

    cost = (
        fresh_text * _rate(prices.input_text)
        + fresh_audio * _rate(prices.input_audio)
        + cached_text * _rate(prices.cached_input_text)
        + cached_audio * _rate(prices.cached_input_audio)
        + output_tokens * _rate(prices.output)
    )
    return float(cost.quantize(_QUANT, rounding=ROUND_HALF_UP))
