"""Robinhood adapter for consolidated 1099 data."""

from pathlib import Path

from app.ingestion.base import BaseAdapter, ImportResult


class RobinhoodAdapter(BaseAdapter):
    """Adapter for parsing Robinhood consolidated 1099 exports."""

    def parse(self, file_path: Path) -> ImportResult:
        """Parse Robinhood export into normalized events."""
        raise NotImplementedError("Robinhood adapter not yet implemented")

    def validate(self, data: ImportResult) -> list[str]:
        """Validate Robinhood data."""
        raise NotImplementedError("Robinhood validation not yet implemented")
