"""Morgan Stanley Shareworks adapter for 1099-B and supplemental data."""

from pathlib import Path

from app.ingestion.base import BaseAdapter
from app.models.equity_event import EquityEvent
from app.models.tax_forms import Form1099B


class ShareworksAdapter(BaseAdapter):
    """Adapter for parsing Morgan Stanley Shareworks exports."""

    def parse(self, file_path: Path) -> list[EquityEvent | Form1099B]:
        """Parse Shareworks CSV export into normalized events."""
        raise NotImplementedError("Shareworks adapter not yet implemented")

    def validate(self, data: list[EquityEvent | Form1099B]) -> list[str]:
        """Validate Shareworks data."""
        raise NotImplementedError("Shareworks validation not yet implemented")
