"""Base PDF extractor interface."""

from abc import ABC, abstractmethod
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any


class BasePDFExtractor(ABC):
    """Abstract base class for all form-specific PDF extractors."""

    @abstractmethod
    def extract(self, text: str, tables: list[list[list[str]]] | None = None) -> dict[str, Any] | list[dict[str, Any]]:
        """Extract structured data from raw PDF text and optional tables.

        Args:
            text: Full text extracted from the PDF (already redacted).
            tables: Optional table data extracted by pdfplumber.

        Returns:
            Dictionary or list of dictionaries matching the JSON schema
            expected by ManualAdapter.
        """
        ...

    @abstractmethod
    def validate_extraction(self, data: dict[str, Any] | list[dict[str, Any]]) -> list[str]:
        """Validate that all required fields were extracted.

        Returns:
            List of validation error messages (empty if valid).
        """
        ...

    def get_warnings(self, data: dict[str, Any] | list[dict[str, Any]]) -> list[str]:
        """Return plausibility warnings that don't block output.

        Override in subclasses to add form-specific sanity checks
        (e.g., OCR error detection). These are printed as warnings
        but do not prevent output generation.

        Returns:
            List of warning messages (empty if no concerns).
        """
        return []

    def _parse_decimal(self, value: str) -> Decimal | None:
        """Parse a string value into Decimal, handling $, commas, parens."""
        if not value or not value.strip():
            return None
        cleaned = value.strip().replace("$", "").replace(",", "")
        # Handle negative values in parentheses: (1234.56) -> -1234.56
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = "-" + cleaned[1:-1]
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return None

    def _parse_date(self, value: str) -> str | None:
        """Parse various date formats into ISO format (YYYY-MM-DD)."""
        if not value or not value.strip():
            return None
        value = value.strip()
        # Try ISO format first
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y"):
            try:
                parsed = date(1, 1, 1)  # placeholder
                from datetime import datetime
                parsed = datetime.strptime(value, fmt).date()
                return parsed.isoformat()
            except ValueError:
                continue
        return None

    def _decimal_to_str(self, val: Decimal | None) -> str | None:
        """Convert Decimal to string for JSON output."""
        if val is None:
            return None
        return str(val.quantize(Decimal("0.01")))
