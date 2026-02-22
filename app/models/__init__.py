"""Data models for TaxBot 9000."""

from app.models.data_gaps import DataGap, DataGapReport, GapCategory, GapSeverity
from app.models.enums import (
    AdjustmentCode,
    BrokerSource,
    DispositionType,
    EquityType,
    FilingStatus,
    Form8949Category,
    HoldingPeriod,
    TransactionType,
)
from app.models.equity_event import EquityEvent, Lot, Sale, SaleResult, Security
from app.models.reports import (
    AMTWorksheetLine,
    AuditEntry,
    ESPPIncomeLine,
    Form8949Line,
    ReconciliationLine,
    TaxEstimate,
)
from app.models.tax_forms import (
    W2,
    Form1099B,
    Form1099DIV,
    Form1099INT,
    Form3921,
    Form3922,
)

__all__ = [
    "AdjustmentCode",
    "AMTWorksheetLine",
    "AuditEntry",
    "BrokerSource",
    "DataGap",
    "DataGapReport",
    "DispositionType",
    "EquityEvent",
    "EquityType",
    "ESPPIncomeLine",
    "FilingStatus",
    "Form1099B",
    "Form1099DIV",
    "Form1099INT",
    "Form3921",
    "Form3922",
    "Form8949Category",
    "Form8949Line",
    "GapCategory",
    "GapSeverity",
    "HoldingPeriod",
    "Lot",
    "ReconciliationLine",
    "Sale",
    "SaleResult",
    "Security",
    "TaxEstimate",
    "TransactionType",
    "W2",
]
