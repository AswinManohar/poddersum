from __future__ import annotations

import pytest

from llm.config import ModelPrices
from llm.pricing import compute_cost

PRICES = ModelPrices(
    input_text=0.25, input_audio=0.50, output=1.50, cached_input_text=0.025, cached_input_audio=0.05
)


def test_text_only():
    assert compute_cost(PRICES, input_tokens=1_000_000, output_tokens=1_000_000) == 1.75


def test_audio_input_at_audio_rate():
    cost = compute_cost(PRICES, input_tokens=1_000_000, output_tokens=0, audio_input_tokens=800_000)
    assert cost == pytest.approx(0.8 * 0.50 + 0.2 * 0.25)


def test_cached_text_discount():
    cost = compute_cost(PRICES, input_tokens=1_000_000, output_tokens=0, cached_tokens=600_000)
    assert cost == pytest.approx(0.4 * 0.25 + 0.6 * 0.025)


def test_cached_audio_discount():
    cost = compute_cost(
        PRICES,
        input_tokens=1_000_000,
        output_tokens=0,
        audio_input_tokens=1_000_000,
        cached_tokens=500_000,
        cached_audio_tokens=500_000,
    )
    assert cost == pytest.approx(0.5 * 0.50 + 0.5 * 0.05)


def test_rounding_to_eight_places():
    # 1 text token = 2.5e-7 -> exact; 3 output tokens = 4.5e-6
    assert compute_cost(PRICES, input_tokens=1, output_tokens=3) == 0.00000475
    # 1 cached text token = 2.5e-8 -> rounds half-up to 3e-8
    assert compute_cost(PRICES, input_tokens=1, output_tokens=0, cached_tokens=1) == 0.00000003


def test_inconsistent_counts_are_clamped():
    # cached larger than input never yields negative fresh tokens
    cost = compute_cost(PRICES, input_tokens=10, output_tokens=0, cached_tokens=50)
    assert cost == pytest.approx(10 * 0.025 / 1e6, abs=1e-8)


def test_negative_counts_rejected():
    with pytest.raises(ValueError):
        compute_cost(PRICES, input_tokens=-1, output_tokens=0)
