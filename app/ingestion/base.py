"""Base adapter interface for data ingestion."""

from abc import ABC, abstractmethod
from pathlib import Path

from app.models.equity_event import EquityEvent
from app.models.tax_forms import Form1099B


class BaseAdapter(ABC):
    """Abstract base class for all ingestion adapters."""

    @abstractmethod
    def parse(self, file_path: Path) -> list[EquityEvent | Form1099B]:
        """Parse a file and return normalized events or 1099-B records."""
        ...

    @abstractmethod
    def validate(self, data: list[EquityEvent | Form1099B]) -> list[str]:
        """Validate parsed data. Returns a list of validation error messages."""
        ...
