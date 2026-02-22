"""Structured data-gap reporting models for TaxBot 9000.

When the reconciliation engine auto-creates lots or detects missing source
documents, the gap analyzer groups them into actionable DataGap records so
the CLI can present a concise summary instead of raw warnings.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field


class GapSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class GapCategory(StrEnum):
    AUTO_CREATED_LOT = "AUTO_CREATED_LOT"
    ZERO_BASIS = "ZERO_BASIS"
    MISSING_FORM_3922 = "MISSING_FORM_3922"
    MISSING_FORM_3921 = "MISSING_FORM_3921"
    SUSPICIOUS_BASIS = "SUSPICIOUS_BASIS"
    PASSTHROUGH_SALE = "PASSTHROUGH_SALE"


class DataGap(BaseModel):
    """A single detected data gap, grouped by ticker and category."""

    category: GapCategory
    severity: GapSeverity
    ticker: str
    summary: str
    missing_document: str = ""
    suggested_action: str = ""
    lot_count: int = 0
    total_basis: Decimal = Decimal("0")
    date_range_start: date | None = None
    date_range_end: date | None = None


class DataGapReport(BaseModel):
    """Aggregate gap report attached to a reconciliation run."""

    gaps: list[DataGap] = Field(default_factory=list)
    total_auto_created_lots: int = 0
    total_zero_basis_sales: int = 0
    total_missing_forms: int = 0

    @property
    def has_blocking_gaps(self) -> bool:
        """True if any gap has ERROR severity."""
        return any(g.severity == GapSeverity.ERROR for g in self.gaps)
