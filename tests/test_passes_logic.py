"""
Pure-logic tests for service/passes.py (no DB, no Stripe, no app import).
Runs standalone:  pytest --noconftest tests/test_passes_logic.py
"""
from datetime import datetime, timedelta

from service import passes

NOW = datetime(2026, 11, 2, 12, 0, 0)


class TestComputeExpiry:

    def test_starts_now_without_an_earlier_pass(self):
        assert passes.compute_expiry(NOW, None, 48) == NOW + timedelta(hours=48)

    def test_stacks_after_a_pass_that_is_still_running(self):
        running_until = NOW + timedelta(hours=10)
        assert passes.compute_expiry(NOW, running_until, 48) == running_until + timedelta(hours=48)

    def test_ignores_an_expired_pass(self):
        assert passes.compute_expiry(NOW, NOW - timedelta(hours=1), 48) == NOW + timedelta(hours=48)

    def test_expiry_exactly_now_counts_as_expired(self):
        assert passes.compute_expiry(NOW, NOW, 24) == NOW + timedelta(hours=24)


class TestClampHours:

    def test_valid_values_pass_through(self):
        assert passes.clamp_hours("48", 99) == 48
        assert passes.clamp_hours(72, 99) == 72

    def test_garbage_falls_back(self):
        for bad in (None, "", "abc", "4.5x", [], {}):
            assert passes.clamp_hours(bad, 48) == 48

    def test_out_of_range_falls_back(self):
        assert passes.clamp_hours(0, 48) == 48
        assert passes.clamp_hours(-5, 48) == 48
        assert passes.clamp_hours(passes.MAX_PASS_HOURS + 1, 48) == 48

    def test_bounds_are_inclusive(self):
        assert passes.clamp_hours(passes.MIN_PASS_HOURS, 48) == passes.MIN_PASS_HOURS
        assert passes.clamp_hours(passes.MAX_PASS_HOURS, 48) == passes.MAX_PASS_HOURS


class TestFormatExpiry:

    def test_winter_time_is_utc_plus_one(self):
        assert passes.format_expiry(datetime(2026, 11, 2, 13, 30), "Europe/Warsaw") == "02.11 14:30"

    def test_summer_time_is_utc_plus_two(self):
        assert passes.format_expiry(datetime(2026, 7, 1, 12, 0), "Europe/Warsaw") == "01.07 14:00"

    def test_none_gives_none(self):
        assert passes.format_expiry(None) is None

    def test_unknown_timezone_falls_back_to_utc(self):
        assert passes.format_expiry(datetime(2026, 11, 2, 13, 30), "Not/AZone") == "02.11 13:30"
