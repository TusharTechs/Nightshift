"""Unit tests: OAuth timestamp freshness (Risk 1 mitigation, Section 8.3)."""

from __future__ import annotations

import pytest

from app.domain.security import TimestampExpiredError, verify_timestamp_freshness


def test_timestamp_within_drift_window_passes():
    verify_timestamp_freshness(request_timestamp=1_000_000, current_timestamp=1_000_200, drift_seconds=300)


def test_timestamp_exactly_at_boundary_passes():
    verify_timestamp_freshness(request_timestamp=1_000_000, current_timestamp=1_000_300, drift_seconds=300)


def test_timestamp_beyond_drift_window_raises():
    with pytest.raises(TimestampExpiredError):
        verify_timestamp_freshness(
            request_timestamp=1_000_000, current_timestamp=1_000_301, drift_seconds=300
        )
