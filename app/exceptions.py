"""Custom exceptions for EquityTax Reconciler."""

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
