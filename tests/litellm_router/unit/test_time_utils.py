"""Unit tests for :class:`litellm_router.utils.time_utils.TimeTracker`."""

import pytest

from litellm_router.utils import time_utils
from litellm_router.utils.time_utils import TimeTracker


def test_calculate_latency_ms_uses_elapsed_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter([1.0, 1.5])
    monkeypatch.setattr(time_utils.time, "time", lambda: next(times))
    start = TimeTracker.get_current_time()
    assert TimeTracker.calculate_latency_ms(start) == pytest.approx(500.0)


def test_calculate_duration_sec_rounds_to_two_dp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter([10.0, 10.12345])
    monkeypatch.setattr(time_utils.time, "time", lambda: next(times))
    start = TimeTracker.get_current_time()
    assert TimeTracker.calculate_duration_sec(start) == 0.12
