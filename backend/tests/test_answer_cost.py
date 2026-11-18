"""G1 budget: a repeat question on a 1-hour episode costs under $0.01 (pure arithmetic).

Uses the real ``models.yaml`` prices for the ``summary`` (ask/presets) and ``chat`` tasks.
Sizes: a 1-hour conversation is ~9-10k words, i.e. ~13-15k tokens of transcript plus the
segment ids/timestamps we add; we budget 15,000 input tokens of which the system prompt +
transcript prefix (~14,500) is served from the implicit prompt cache on a repeat request,
and ~600 output tokens (claims; thinking at low effort included).
"""

from __future__ import annotations

import pytest

from llm.config import load_config
from llm.pricing import compute_cost

INPUT_TOKENS = 15_000
CACHED_PREFIX_TOKENS = 14_500
OUTPUT_TOKENS = 600
G1_BUDGET_USD = 0.01


@pytest.mark.parametrize("task", ["summary", "chat"])
def test_repeat_request_on_one_hour_episode_under_one_cent(task: str) -> None:
    _, model = load_config().resolve(task)
    prices = model.prices_per_1m_usd
    repeat = compute_cost(
        prices,
        input_tokens=INPUT_TOKENS,
        output_tokens=OUTPUT_TOKENS,
        cached_tokens=CACHED_PREFIX_TOKENS,
    )
    assert repeat < G1_BUDGET_USD, f"repeat {task} request costs ${repeat:.5f}"
    # Even with no cache hit at all, the first request stays within budget at these prices.
    first = compute_cost(prices, input_tokens=INPUT_TOKENS, output_tokens=OUTPUT_TOKENS)
    assert repeat < first < G1_BUDGET_USD
