"""Custom exceptions for TaxBot 9000."""

from decimal import Decimal


class TaxComputationError(Exception):
    """Base exception for tax computation errors."""


class BasisMismatchError(TaxComputationError):
    """Raised when broker-reported basis doesn't match computed basis."""

    def __init__(self, lot_id: str, broker_basis: Decimal, computed_basis: Decimal):
        self.lot_id = lot_id
        self.broker_basis = broker_basis
        self.computed_basis = computed_basis
        super().__init__(
            f"Basis mismatch for lot {lot_id}: "
            f"broker={broker_basis}, computed={computed_basis}"
        )


class LotNotFoundError(TaxComputationError):
    """Raised when a sale references a lot that doesn't exist."""

    def __init__(self, lot_id: str):
        self.lot_id = lot_id
        super().__init__(f"Lot not found: {lot_id}")


class InsufficientSharesError(TaxComputationError):
    """Raised when a sale requires more shares than available in a lot."""

    def __init__(self, lot_id: str, requested: Decimal, available: Decimal):
        self.lot_id = lot_id
        self.requested = requested
        self.available = available
        super().__init__(
            f"Insufficient shares in lot {lot_id}: "
            f"requested={requested}, available={available}"
        )


class DataValidationError(TaxComputationError):
    """Raised when input data fails validation."""

    def __init__(self, field: str, message: str):
        self.field = field
        super().__init__(f"Validation error on '{field}': {message}")


class ImportError(TaxComputationError):
    """Raised when data import fails."""

    def __init__(self, source: str, message: str):
        self.source = source
        super().__init__(f"Import error from {source}: {message}")


class ReconciliationError(TaxComputationError):
    """Raised when reconciliation produces unresolvable discrepancies."""

    def __init__(self, message: str):
        super().__init__(f"Reconciliation error: {message}")


class PDFParseError(TaxComputationError):
    """Raised when PDF parsing fails."""

    def __init__(self, file_path: str, message: str):
        self.file_path = file_path
        super().__init__(f"PDF parse error for {file_path}: {message}")


class FormDetectionError(PDFParseError):
    """Raised when form type cannot be auto-detected."""

    def __init__(self, file_path: str):
        super().__init__(file_path, "Cannot auto-detect form type. Use --form-type to specify.")


class ExtractionError(PDFParseError):
    """Raised when required fields cannot be extracted from the PDF."""

    def __init__(self, file_path: str, fields: list[str]):
        self.fields = fields
        super().__init__(file_path, f"Could not extract required fields: {', '.join(fields)}")


class VisionExtractionError(PDFParseError):
    """Raised when Claude Vision API extraction fails."""

    def __init__(self, file_path: str, message: str):
        super().__init__(file_path, f"Vision extraction failed: {message}")


class SaleMatchError(TaxComputationError):
    """Raised when a sale cannot be matched to any lot."""

    def __init__(self, sale_id: str, ticker: str):
        self.sale_id = sale_id
        self.ticker = ticker
        super().__init__(f"No matching lot found for sale {sale_id} ({ticker})")


class MissingEventDataError(TaxComputationError):
    """Raised when required event data (Form 3921/3922) is missing for basis correction."""

    def __init__(self, lot_id: str, equity_type: str):
        self.lot_id = lot_id
        self.equity_type = equity_type
        super().__init__(
            f"Missing source event data for {equity_type} lot {lot_id}"
        )
