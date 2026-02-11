"""Robinhood adapter for consolidated 1099 data."""

from pathlib import Path

from app.ingestion.base import BaseAdapter
from app.models.equity_event import EquityEvent
from app.models.tax_forms import Form1099B


class RobinhoodAdapter(BaseAdapter):
    """Adapter for parsing Robinhood consolidated 1099 exports."""

    def parse(self, file_path: Path) -> list[EquityEvent | Form1099B]:
        """Parse Robinhood export into normalized events."""
        raise NotImplementedError("Robinhood adapter not yet implemented")

    def validate(self, data: list[EquityEvent | Form1099B]) -> list[str]:
        """Validate Robinhood data."""
        raise NotImplementedError("Robinhood validation not yet implemented")
