"""Robinhood consolidated 1099 PDF extractor.

Robinhood issues a single PDF containing 1099-DIV, 1099-INT, 1099-B,
and 1099-MISC data. The summary page uses a format like:
    "1a- Total ordinary dividends ... 3,475.63"
which differs from the standard "Box 1a" format used by other brokers.
"""

import re
from decimal import Decimal
from typing import Any

from app.parsing.base import BasePDFExtractor
from app.parsing.extractors.form_1099b import Form1099BExtractor


class RobinhoodConsolidatedExtractor(BasePDFExtractor):
    """Extracts 1099-DIV, 1099-INT, and 1099-B from a Robinhood consolidated PDF."""

    # --- 1099-DIV patterns (Robinhood summary format) ---
    DIV_ORDINARY = re.compile(
        r"1a-\s*Total\s+ordinary\s+dividends.*?([\d,]+\.\d{2})", re.IGNORECASE
    )
    DIV_QUALIFIED = re.compile(
        r"1b-\s*Qualified\s+dividends.*?([\d,]+\.\d{2})", re.IGNORECASE
    )
    DIV_CAP_GAIN = re.compile(
        r"2a-\s*Total\s+capital\s+gain\s+distr.*?([\d,]+\.\d{2})", re.IGNORECASE
    )
    DIV_NONDIV = re.compile(
        r"3-\s*Nondividend\s+distributions.*?([\d,]+\.\d{2})", re.IGNORECASE
    )
    DIV_SEC_199A = re.compile(
        r"5-\s*Section\s*199A\s+dividends.*?([\d,]+\.\d{2})", re.IGNORECASE
    )
    DIV_FOREIGN_TAX = re.compile(
        r"7-\s*Foreign\s+tax\s+paid.*?([\d,]+\.\d{2})", re.IGNORECASE
    )
    DIV_FED_TAX = re.compile(
        r"4-\s*Federal\s+income\s+tax\s+withheld.*?([\d,]+\.\d{2})", re.IGNORECASE
    )

    # --- 1099-INT patterns (Robinhood summary format) ---
    INT_INCOME = re.compile(
        r"1-\s+Interest\s+income.*?([\d,]+\.\d{2})", re.IGNORECASE
    )
    INT_PENALTY = re.compile(
        r"2-\s+Early\s+withdrawal\s+penalty.*?([\d,]+\.\d{2})", re.IGNORECASE
    )
    INT_US_BOND = re.compile(
        r"3-\s+Interest\s+on\s+U\.?S\.?\s+Savings\s+Bonds.*?([\d,]+\.\d{2})", re.IGNORECASE
    )
    INT_FED_TAX = re.compile(
        r"4-\s+Federal\s+income\s+tax\s+withheld.*?([\d,]+\.\d{2})", re.IGNORECASE
    )

    PAYER_PATTERN = re.compile(r"Robinhood\s+Securities\s+LLC", re.IGNORECASE)

    def extract(self, text: str, tables: list[list[list[str]]] | None = None) -> dict[str, Any]:
        """Extract consolidated 1099 data from Robinhood PDF."""
        result: dict[str, Any] = {
            "consolidated": True,
            "payer_name": "Robinhood Securities LLC",
        }

        # Tax year
        year_match = re.search(r"\b(20\d{2})\b", text)
        if year_match:
            result["tax_year"] = int(year_match.group(1))

        # Split text into sections to avoid cross-form matching.
        # Robinhood PDFs have sections headed by "Form 1099-DIV", "Form 1099-INT", etc.
        div_section = self._extract_section(text, "1099-DIV")
        int_section = self._extract_section(text, "1099-INT")

        # Parse 1099-DIV
        div_data = self._extract_div(div_section or text)
        if div_data:
            if "tax_year" in result:
                div_data["tax_year"] = result["tax_year"]
            div_data["payer_name"] = result["payer_name"]
            result["form_1099div"] = div_data

        # Parse 1099-INT
        int_data = self._extract_int(int_section or text)
        if int_data:
            if "tax_year" in result:
                int_data["tax_year"] = result["tax_year"]
            int_data["payer_name"] = result["payer_name"]
            result["form_1099int"] = int_data

        # Parse 1099-B via delegation
        b_extractor = Form1099BExtractor()
        b_records = b_extractor.extract(text, tables=tables)
        # Filter out all-zero records
        non_zero = [
            r for r in b_records
            if self._has_nonzero_amounts(r)
        ]
        if non_zero:
            result["form_1099b"] = non_zero

        return result

    def _extract_section(self, text: str, form_label: str) -> str | None:
        """Extract the text section for a specific form type."""
        pattern = re.compile(
            rf"(Form\s+{re.escape(form_label)}\b.*?)(?=Form\s+1099-|$)",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.search(text)
        return match.group(1) if match else None

    def _extract_div(self, text: str) -> dict[str, Any] | None:
        """Parse 1099-DIV fields from the Robinhood summary format."""
        data: dict[str, Any] = {}

        match = self.DIV_ORDINARY.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                data["ordinary_dividends"] = self._decimal_to_str(parsed)

        match = self.DIV_QUALIFIED.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                data["qualified_dividends"] = self._decimal_to_str(parsed)

        match = self.DIV_CAP_GAIN.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                data["capital_gain_distributions"] = self._decimal_to_str(parsed)

        match = self.DIV_NONDIV.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                data["nondividend_distributions"] = self._decimal_to_str(parsed)

        match = self.DIV_SEC_199A.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                data["section_199a_dividends"] = self._decimal_to_str(parsed)

        match = self.DIV_FOREIGN_TAX.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                data["foreign_tax_paid"] = self._decimal_to_str(parsed)

        match = self.DIV_FED_TAX.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                data["federal_tax_withheld"] = self._decimal_to_str(parsed)

        return data if data else None

    def _extract_int(self, text: str) -> dict[str, Any] | None:
        """Parse 1099-INT fields from the Robinhood summary format."""
        data: dict[str, Any] = {}

        match = self.INT_INCOME.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                data["interest_income"] = self._decimal_to_str(parsed)

        match = self.INT_PENALTY.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                data["early_withdrawal_penalty"] = self._decimal_to_str(parsed)

        match = self.INT_US_BOND.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                data["us_savings_bond_interest"] = self._decimal_to_str(parsed)

        match = self.INT_FED_TAX.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                data["federal_tax_withheld"] = self._decimal_to_str(parsed)

        return data if data else None

    @staticmethod
    def _has_nonzero_amounts(record: dict[str, Any]) -> bool:
        """Check if a 1099-B record has any non-zero monetary values."""
        for field in ("proceeds", "cost_basis"):
            val = record.get(field)
            if val is not None:
                try:
                    if Decimal(str(val)) != 0:
                        return True
                except Exception:
                    pass
        return False

    def validate_extraction(self, data: dict[str, Any] | list[dict[str, Any]]) -> list[str]:
        """Validate that at least one sub-form has data."""
        if isinstance(data, list):
            data = data[0] if data else {}
        errors: list[str] = []

        has_div = bool(data.get("form_1099div"))
        has_int = bool(data.get("form_1099int"))
        has_b = bool(data.get("form_1099b"))

        if not (has_div or has_int or has_b):
            errors.append("Robinhood consolidated: no sub-form data extracted (1099-DIV, 1099-INT, or 1099-B)")

        if has_div:
            div = data["form_1099div"]
            if "ordinary_dividends" not in div:
                errors.append("Robinhood consolidated 1099-DIV: missing ordinary_dividends")

        if has_int:
            int_data = data["form_1099int"]
            if "interest_income" not in int_data:
                errors.append("Robinhood consolidated 1099-INT: missing interest_income")

        return errors
