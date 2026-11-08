"""D65: the bench cost model bills cached input tokens at Groq's half price."""

import pytest

from bench import live


def test_cost_with_and_without_cached_input():
    assert live.cost(9115, 7718) == pytest.approx(0.00599805, abs=1e-9)
    # every input token cached at 50 % off saves 9115 * 0.15 / 2 per million
    assert live.cost(9115, 7718, 9115) == pytest.approx(0.00599805 - 9115 * 0.15 / 2 / 1e6, abs=1e-9)
    assert live.cost(100, 0, 500) == live.cost(100, 0, 100)  # cached is capped at tokens_in
