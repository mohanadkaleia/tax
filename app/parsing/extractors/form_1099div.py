"""Form 1099-DIV (Dividends) PDF extractor."""

import re
from typing import Any

from app.parsing.base import BasePDFExtractor


class Form1099DIVExtractor(BasePDFExtractor):
    """Extracts Form 1099-DIV data from PDF text."""

    PAYER_PATTERN = re.compile(
        r"(?:Payer|Filer)(?:'?s?)?\s*name[^\n]*?\n\s*(.+?)(?:\n|$)", re.IGNORECASE
    )
    ORDINARY_DIV_PATTERN = re.compile(
        r"(?:Box\s*1a|1a\s+(?:Total\s+)?[Oo]rdinary\s+dividends)[^\d$]*\$?([\d,]+\.\d{2})", re.IGNORECASE
    )
    QUALIFIED_DIV_PATTERN = re.compile(
        r"(?:Box\s*1b|1b\s+[Qq]ualified\s+dividends)[^\d$]*\$?([\d,]+\.\d{2})", re.IGNORECASE
    )
    CAP_GAIN_PATTERN = re.compile(
        r"(?:Box\s*2a|2a\s+(?:Total\s+)?[Cc]apital\s+gain)[^\d$]*\$?([\d,]+\.\d{2})", re.IGNORECASE
    )
    NONDIV_DIST_PATTERN = re.compile(
        r"(?:Box\s*3|3\s+[Nn]ondividend\s+distributions)[^\d$]*\$?([\d,]+\.\d{2})", re.IGNORECASE
    )
    SEC_199A_PATTERN = re.compile(
        r"(?:Box\s*5|5\s+[Ss]ection\s*199A\s+dividends)[^\d$]*\$?([\d,]+\.\d{2})", re.IGNORECASE
    )
    FOREIGN_TAX_PATTERN = re.compile(
        r"(?:Box\s*(?:6|7)|(?:6|7)\s+[Ff]oreign\s+tax\s+paid)[^\d$]*\$?([\d,]+\.\d{2})", re.IGNORECASE
    )
    FOREIGN_COUNTRY_PATTERN = re.compile(
        r"(?:Box\s*(?:7|8)|(?:7|8)\s+[Ff]oreign\s+country)[^\d$]*\s+([A-Za-z\s]+?)(?:\n|$)", re.IGNORECASE
    )
    FED_TAX_PATTERN = re.compile(
        r"(?:Box\s*4|4\s+[Ff]ederal\s*income\s*tax\s*withheld)[^\d$]*\$?([\d,]+\.\d{2})", re.IGNORECASE
    )

    def extract(self, text: str, tables: list[list[list[str]]] | None = None) -> dict[str, Any]:
        """Extract 1099-DIV fields from PDF text."""
        result: dict[str, Any] = {}

        # Tax year
        year_match = re.search(r"\b(20\d{2})\b", text)
        if year_match:
            result["tax_year"] = int(year_match.group(1))

        # Payer name
        payer_match = self.PAYER_PATTERN.search(text)
        if payer_match:
            result["broker_name"] = payer_match.group(1).strip()

        # Box 1a - Ordinary dividends
        match = self.ORDINARY_DIV_PATTERN.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                result["ordinary_dividends"] = self._decimal_to_str(parsed)

        # Box 1b - Qualified dividends
        match = self.QUALIFIED_DIV_PATTERN.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                result["qualified_dividends"] = self._decimal_to_str(parsed)

        # Box 2a - Capital gain distributions
        match = self.CAP_GAIN_PATTERN.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                result["total_capital_gain_distributions"] = self._decimal_to_str(parsed)

        # Box 3 - Nondividend distributions
        match = self.NONDIV_DIST_PATTERN.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                result["nondividend_distributions"] = self._decimal_to_str(parsed)

        # Box 5 - Section 199A dividends
        match = self.SEC_199A_PATTERN.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                result["section_199a_dividends"] = self._decimal_to_str(parsed)

        # Box 7 (or Box 6) - Foreign tax paid
        match = self.FOREIGN_TAX_PATTERN.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                result["foreign_tax_paid"] = self._decimal_to_str(parsed)

        # Box 7 country (or Box 8) - Foreign country
        match = self.FOREIGN_COUNTRY_PATTERN.search(text)
        if match:
            country = match.group(1).strip()
            if country:
                result["foreign_country"] = country

        # Box 4 - Federal tax withheld
        match = self.FED_TAX_PATTERN.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                result["federal_tax_withheld"] = self._decimal_to_str(parsed)

        return result

    def validate_extraction(self, data: dict[str, Any] | list[dict[str, Any]]) -> list[str]:
        """Validate 1099-DIV extraction."""
        if isinstance(data, list):
            data = data[0] if data else {}
        errors: list[str] = []
        required = ["tax_year", "ordinary_dividends", "qualified_dividends"]
        for field in required:
            if field not in data or data[field] is None:
                errors.append(f"Missing required 1099-DIV field: {field}")
        return errors
