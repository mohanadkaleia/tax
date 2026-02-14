"""Form 1099-INT (Interest Income) PDF extractor."""

import re
from typing import Any

from app.parsing.base import BasePDFExtractor


class Form1099INTExtractor(BasePDFExtractor):
    """Extracts Form 1099-INT data from PDF text."""

    PAYER_PATTERN = re.compile(
        r"(?:Payer|Filer)(?:'?s?)?\s*name[^\n]*?\n\s*(.+?)(?:\n|$)", re.IGNORECASE
    )
    INTEREST_PATTERN = re.compile(
        r"(?:Box\s*1|1\s+[Ii]nterest\s+[Ii]ncome)[^\d$]*\$?([\d,]+\.\d{2})", re.IGNORECASE
    )
    PENALTY_PATTERN = re.compile(
        r"(?:Box\s*2|2\s+[Ee]arly\s+withdrawal\s+penalty)[^\d$]*\$?([\d,]+\.\d{2})", re.IGNORECASE
    )
    US_BOND_PATTERN = re.compile(
        r"(?:Box\s*3|3\s+[Ii]nterest\s+on\s+U\.?S\.?\s+[Ss]avings\s+[Bb]onds)[^\d$]*\$?([\d,]+\.\d{2})", re.IGNORECASE
    )
    FED_TAX_PATTERN = re.compile(
        r"(?:Box\s*4|4\s+[Ff]ederal\s*income\s*tax\s*withheld)[^\d$]*\$?([\d,]+\.\d{2})", re.IGNORECASE
    )

    def extract(self, text: str, tables: list[list[list[str]]] | None = None) -> dict[str, Any]:
        """Extract 1099-INT fields from PDF text."""
        result: dict[str, Any] = {}

        # Tax year
        year_match = re.search(r"\b(20\d{2})\b", text)
        if year_match:
            result["tax_year"] = int(year_match.group(1))

        # Payer name
        payer_match = self.PAYER_PATTERN.search(text)
        if payer_match:
            result["payer_name"] = payer_match.group(1).strip()

        # Box 1 - Interest income
        match = self.INTEREST_PATTERN.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                result["interest_income"] = self._decimal_to_str(parsed)

        # Box 2 - Early withdrawal penalty
        match = self.PENALTY_PATTERN.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                result["early_withdrawal_penalty"] = self._decimal_to_str(parsed)

        # Box 3 - Interest on US Savings Bonds and Treasury obligations
        match = self.US_BOND_PATTERN.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                result["us_savings_bond_interest"] = self._decimal_to_str(parsed)

        # Box 4 - Federal tax withheld
        match = self.FED_TAX_PATTERN.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                result["federal_tax_withheld"] = self._decimal_to_str(parsed)

        return result

    def validate_extraction(self, data: dict[str, Any] | list[dict[str, Any]]) -> list[str]:
        """Validate 1099-INT extraction."""
        if isinstance(data, list):
            data = data[0] if data else {}
        errors: list[str] = []
        required = ["tax_year", "interest_income"]
        for field in required:
            if field not in data or data[field] is None:
                errors.append(f"Missing required 1099-INT field: {field}")
        return errors
