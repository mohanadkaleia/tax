"""Tests for event normalization."""

from app.models.equity_event import EquityEvent
from app.normalization.events import EventNormalizer


class TestEventNormalizer:
    def test_normalize_empty(self):
        normalizer = EventNormalizer()
        result = normalizer.normalize([])
        assert result == []

    def test_deduplicate_events(self, sample_vest_event: EquityEvent):
        normalizer = EventNormalizer()
        # Same event twice
        result = normalizer.normalize([sample_vest_event, sample_vest_event])
        assert len(result) == 1
