"""Manual entry adapter for W-2, Form 3921, Form 3922 data."""

from pathlib import Path

from app.ingestion.base import BaseAdapter
from app.models.equity_event import EquityEvent
from app.models.tax_forms import Form1099B


class ManualAdapter(BaseAdapter):
    """Adapter for manually entered tax form data (W-2, 3921, 3922)."""

    def parse(self, file_path: Path) -> list[EquityEvent | Form1099B]:
        """Parse manual entry file (JSON/CSV) into normalized events."""
        raise NotImplementedError("Manual adapter not yet implemented")

    def validate(self, data: list[EquityEvent | Form1099B]) -> list[str]:
        """Validate manual entry data."""
        raise NotImplementedError("Manual validation not yet implemented")
