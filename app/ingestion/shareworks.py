"""Morgan Stanley Shareworks adapter for 1099-B and supplemental data."""

from pathlib import Path

from app.ingestion.base import BaseAdapter, ImportResult


class ShareworksAdapter(BaseAdapter):
    """Adapter for parsing Morgan Stanley Shareworks exports."""

    def parse(self, file_path: Path) -> ImportResult:
        """Parse Shareworks CSV export into normalized events."""
        raise NotImplementedError("Shareworks adapter not yet implemented")

    def validate(self, data: ImportResult) -> list[str]:
        """Validate Shareworks data."""
        raise NotImplementedError("Shareworks validation not yet implemented")
