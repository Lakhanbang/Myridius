"""Helpers for measuring how long something took."""

import time


class TimeTracker:
    """Small timing helpers."""

    @staticmethod
    def get_current_time() -> float:
        """Return the current time in seconds (as a number)."""
        return time.time()

    @staticmethod
    def calculate_latency_ms(start_time: float) -> float:
        """Return how many milliseconds have passed since `start_time`."""
        return (time.time() - start_time) * 1000.0

    @staticmethod
    def calculate_duration_sec(start_time: float) -> float:
        """Return how many seconds have passed since `start_time` (2 decimals)."""
        return round(time.time() - start_time, 2)
