"""Event normalization: deduplication and validation."""

from app.models.equity_event import EquityEvent


class EventNormalizer:
    """Normalizes raw equity events: deduplication, validation, enrichment."""

    def normalize(self, raw_events: list[EquityEvent]) -> list[EquityEvent]:
        """Normalize a list of raw events into deduplicated, validated events."""
        validated = self._validate(raw_events)
        deduplicated = self._deduplicate(validated)
        return deduplicated

    def _validate(self, events: list[EquityEvent]) -> list[EquityEvent]:
        """Validate event data integrity."""
        # TODO: Implement validation rules per CPA plan
        return events

    def _deduplicate(self, events: list[EquityEvent]) -> list[EquityEvent]:
        """Remove duplicate events (same event imported from multiple sources)."""
        # TODO: Implement deduplication logic
        seen_ids: set[str] = set()
        unique: list[EquityEvent] = []
        for event in events:
            if event.id not in seen_ids:
                seen_ids.add(event.id)
                unique.append(event)
        return unique
