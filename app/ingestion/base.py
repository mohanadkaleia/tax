"""Base adapter interface for data ingestion."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from app.models.equity_event import EquityEvent, Lot, Sale
from app.parsing.detector import FormType


@dataclass
class ImportResult:
    """Bundles the output from an adapter's parse method."""

    form_type: FormType
    tax_year: int
    forms: list = field(default_factory=list)
    events: list[EquityEvent] = field(default_factory=list)
    lots: list[Lot] = field(default_factory=list)
    sales: list[Sale] = field(default_factory=list)


class BaseAdapter(ABC):
    """Abstract base class for all ingestion adapters."""

    @abstractmethod
    def parse(self, file_path: Path) -> ImportResult:
        """Parse a file and return an ImportResult with typed models."""
        ...

    @abstractmethod
    def validate(self, data: ImportResult) -> list[str]:
        """Validate parsed data. Returns a list of validation error messages."""
        ...
