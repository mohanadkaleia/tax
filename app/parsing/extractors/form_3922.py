"""Form 3922 (ESPP Transfer) PDF extractor."""

import re
from typing import Any

from app.parsing.base import BasePDFExtractor


class Form3922Extractor(BasePDFExtractor):
    """Extracts Form 3922 data from PDF text."""

    OFFERING_DATE_PATTERN = re.compile(
        r"(?:Box\s*1|1\s+Date\s*(?:of\s*)?(?:option\s*)?grant|[Oo]ffering\s*[Dd]ate)[^\d]*([\d/\-]+)",
        re.IGNORECASE,
    )
    PURCHASE_DATE_PATTERN = re.compile(
        r"(?:Box\s*2|2\s+Date\s*(?:of\s*)?transfer|[Pp]urchase\s*[Dd]ate|[Tt]ransfer\s*[Dd]ate)[^\d]*([\d/\-]+)",
        re.IGNORECASE,
    )
    FMV_OFFERING_PATTERN = re.compile(
        r"(?:Box\s*3|3\s+FMV.*?(?:grant|offering)\s*date)[^\d$]*\$?([\d,]+\.\d{2})", re.IGNORECASE
    )
    FMV_PURCHASE_PATTERN = re.compile(
        r"(?:Box\s*4|4\s+FMV.*?(?:transfer|purchase)\s*date)[^\d$]*\$?([\d,]+\.\d{2})", re.IGNORECASE
    )
    PURCHASE_PRICE_PATTERN = re.compile(
        r"(?:Box\s*5|5\s+(?:Price|Cost)\s*(?:paid\s*)?per\s*share)[^\d$]*\$?([\d,]+\.\d{2})", re.IGNORECASE
    )
    SHARES_PATTERN = re.compile(
        r"(?:Box\s*6|6\s+No\.?\s*(?:of\s*)?shares\s*transferred)[^\d]*([\d,]+)", re.IGNORECASE
    )
    EMPLOYER_PATTERN = re.compile(
        r"(?:Transferor|Corporation|Employer)(?:'?s?)?\s*name[^\n]*?\n\s*(.+?)(?:\n|$)", re.IGNORECASE
    )

    def extract(self, text: str, tables: list[list[list[str]]] | None = None) -> list[dict[str, Any]]:
        """Extract Form 3922 records from PDF text."""
        record: dict[str, Any] = {}

        # Tax year
        year_match = re.search(r"\b(20\d{2})\b", text)
        if year_match:
            record["tax_year"] = int(year_match.group(1))

        # Employer
        emp_match = self.EMPLOYER_PATTERN.search(text)
        if emp_match:
            record["employer_name"] = emp_match.group(1).strip()

        # Offering date (Box 1)
        match = self.OFFERING_DATE_PATTERN.search(text)
        if match:
            record["offering_date"] = self._parse_date(match.group(1))

        # Purchase/transfer date (Box 2)
        match = self.PURCHASE_DATE_PATTERN.search(text)
        if match:
            record["purchase_date"] = self._parse_date(match.group(1))

        # FMV on offering date (Box 3)
        match = self.FMV_OFFERING_PATTERN.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                record["fmv_on_offering_date"] = self._decimal_to_str(parsed)

        # FMV on purchase date (Box 4)
        match = self.FMV_PURCHASE_PATTERN.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                record["fmv_on_purchase_date"] = self._decimal_to_str(parsed)

        # Purchase price per share (Box 5)
        match = self.PURCHASE_PRICE_PATTERN.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                record["purchase_price_per_share"] = self._decimal_to_str(parsed)

        # Shares transferred (Box 6)
        match = self.SHARES_PATTERN.search(text)
        if match:
            record["shares_transferred"] = match.group(1).replace(",", "")

        return [record] if record else []

    def validate_extraction(self, data: dict[str, Any] | list[dict[str, Any]]) -> list[str]:
        """Validate Form 3922 extraction."""
        records = data if isinstance(data, list) else [data]
        errors: list[str] = []
        required = [
            "tax_year", "purchase_date", "fmv_on_purchase_date",
            "purchase_price_per_share", "shares_transferred",
        ]
        for i, record in enumerate(records):
            for field in required:
                if field not in record or record[field] is None:
                    errors.append(f"Record {i + 1}: Missing required Form 3922 field: {field}")
        return errors
