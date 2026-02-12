"""Form 3921 (ISO Exercise) PDF extractor."""

import re
from typing import Any

from app.parsing.base import BasePDFExtractor


class Form3921Extractor(BasePDFExtractor):
    """Extracts Form 3921 data from PDF text."""

    # Form 3921 box patterns
    GRANT_DATE_PATTERN = re.compile(
        r"(?:Box\s*1|1\s+Date\s*(?:of\s*)?grant|[Gg]rant\s*[Dd]ate)[^\d]*([\d/\-]+)", re.IGNORECASE
    )
    EXERCISE_DATE_PATTERN = re.compile(
        r"(?:Box\s*2|2\s+Date\s*(?:of\s*)?exercise|[Ee]xercise\s*[Dd]ate)[^\d]*([\d/\-]+)", re.IGNORECASE
    )
    EXERCISE_PRICE_PATTERN = re.compile(
        r"(?:Box\s*3|3\s+Exercise\s*price\s*per\s*share)[^\d$]*\$?([\d,]+\.\d{2})", re.IGNORECASE
    )
    FMV_PATTERN = re.compile(
        r"(?:Box\s*4|4\s+Fair\s*market\s*value.*?per\s*share.*?exercise)[^\d$]*\$?([\d,]+\.\d{2})", re.IGNORECASE
    )
    SHARES_PATTERN = re.compile(
        r"(?:Box\s*5|5\s+No\.?\s*(?:of\s*)?shares\s*transferred)[^\d]*([\d,]+)", re.IGNORECASE
    )
    EMPLOYER_PATTERN = re.compile(
        r"(?:Transferor|Corporation|Employer)(?:'?s?)?\s*name[^\n]*?\n\s*(.+?)(?:\n|$)", re.IGNORECASE
    )

    def extract(self, text: str, tables: list[list[list[str]]] | None = None) -> list[dict[str, Any]]:
        """Extract Form 3921 records from PDF text."""
        record: dict[str, Any] = {}

        # Tax year
        year_match = re.search(r"\b(20\d{2})\b", text)
        if year_match:
            record["tax_year"] = int(year_match.group(1))

        # Employer
        emp_match = self.EMPLOYER_PATTERN.search(text)
        if emp_match:
            record["employer_name"] = emp_match.group(1).strip()

        # Grant date (Box 1)
        match = self.GRANT_DATE_PATTERN.search(text)
        if match:
            record["grant_date"] = self._parse_date(match.group(1))

        # Exercise date (Box 2)
        match = self.EXERCISE_DATE_PATTERN.search(text)
        if match:
            record["exercise_date"] = self._parse_date(match.group(1))

        # Exercise price per share (Box 3)
        match = self.EXERCISE_PRICE_PATTERN.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                record["exercise_price_per_share"] = self._decimal_to_str(parsed)

        # FMV on exercise date (Box 4)
        match = self.FMV_PATTERN.search(text)
        if match:
            parsed = self._parse_decimal(match.group(1))
            if parsed is not None:
                record["fmv_on_exercise_date"] = self._decimal_to_str(parsed)

        # Shares transferred (Box 5)
        match = self.SHARES_PATTERN.search(text)
        if match:
            record["shares_transferred"] = match.group(1).replace(",", "")

        return [record] if record else []

    def validate_extraction(self, data: dict[str, Any] | list[dict[str, Any]]) -> list[str]:
        """Validate Form 3921 extraction."""
        records = data if isinstance(data, list) else [data]
        errors: list[str] = []
        required = [
            "tax_year", "exercise_date", "exercise_price_per_share",
            "fmv_on_exercise_date", "shares_transferred",
        ]
        for i, record in enumerate(records):
            for field in required:
                if field not in record or record[field] is None:
                    errors.append(f"Record {i + 1}: Missing required Form 3921 field: {field}")
        return errors
