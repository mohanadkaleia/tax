"""W-2 PDF extractor."""

import re
from decimal import Decimal
from typing import Any

from app.parsing.base import BasePDFExtractor


class W2Extractor(BasePDFExtractor):
    """Extracts W-2 form data from PDF text."""

    # Regex patterns for W-2 box values
    # These match common W-2 PDF layouts where the box label precedes the value
    PATTERNS = {
        "box1_wages": re.compile(
            r"(?:1\s+Wages,?\s*tips,?\s*other\s*comp|Box\s*1\b)[^\d$]*?([\d,]+\.\d{2})", re.IGNORECASE
        ),
        "box2_federal_withheld": re.compile(
            r"(?:2\s+Federal\s*income\s*tax\s*withheld|Box\s*2\b)[^\d$]*?([\d,]+\.\d{2})", re.IGNORECASE
        ),
        "box3_ss_wages": re.compile(
            r"(?:3\s+Social\s*security\s*wages|Box\s*3\b)[^\d$]*?([\d,]+\.\d{2})", re.IGNORECASE
        ),
        "box4_ss_withheld": re.compile(
            r"(?:4\s+Social\s*security\s*tax\s*withheld|Box\s*4\b)[^\d$]*?([\d,]+\.\d{2})", re.IGNORECASE
        ),
        "box5_medicare_wages": re.compile(
            r"(?:5\s+Medicare\s*wages\s*(?:and\s*tips)?|Box\s*5\b)[^\d$]*?([\d,]+\.\d{2})", re.IGNORECASE
        ),
        "box6_medicare_withheld": re.compile(
            r"(?:6\s+Medicare\s*tax\s*withheld|Box\s*6\b)[^\d$]*?([\d,]+\.\d{2})", re.IGNORECASE
        ),
        "box16_state_wages": re.compile(
            r"(?:16\s+State\s*wages|Box\s*16\b)[^\d$]*?([\d,]+\.\d{2})", re.IGNORECASE
        ),
        "box17_state_withheld": re.compile(
            r"(?:17\s+State\s*income\s*tax|Box\s*17\b)[^\d$]*?([\d,]+\.\d{2})", re.IGNORECASE
        ),
    }

    EMPLOYER_PATTERN = re.compile(
        r"(?:Employer'?s?\s*name|[Cc]\s+Employer'?s?\s*name)[^\n]*?\n\s*(.+?)(?:\n|$)", re.IGNORECASE
    )

    EIN_PATTERN = re.compile(
        r"(?:Employer'?s?\s*(?:identification\s*number|EIN|ID\s*no)|[Bb]\s+Employer'?s?\s*(?:identification|EIN))"
        r"[^\d]*?(\d{2}-\d{7})",
        re.IGNORECASE,
    )

    TAX_YEAR_PATTERN = re.compile(r"(?:Tax\s*[Yy]ear|20\d{2})\s*(\d{4})?")

    BOX12_PATTERN = re.compile(
        r"12[a-d]?\s+(?:See\s+inst.*?)?([A-Z]{1,2})\s+([\d,]+\.\d{2})", re.IGNORECASE
    )

    BOX14_PATTERN = re.compile(
        r"(?:14\s+Other|Box\s*14)[^\n]*\n((?:.*\n)*?)", re.IGNORECASE
    )

    BOX14_ENTRY = re.compile(r"(RSU|ESPP|NSO|ISO|NQSO)\s+([\d,]+\.\d{2})", re.IGNORECASE)

    def extract(self, text: str, tables: list[list[list[str]]] | None = None) -> dict[str, Any]:
        """Extract W-2 fields from PDF text."""
        result: dict[str, Any] = {}

        # Extract tax year
        year_match = re.search(r"\b(20\d{2})\b", text)
        if year_match:
            result["tax_year"] = int(year_match.group(1))

        # Extract employer name
        emp_match = self.EMPLOYER_PATTERN.search(text)
        if emp_match:
            result["employer_name"] = emp_match.group(1).strip()

        # Extract employer EIN
        ein_match = self.EIN_PATTERN.search(text)
        if ein_match:
            result["employer_ein"] = ein_match.group(1)

        # Extract monetary box values
        for field, pattern in self.PATTERNS.items():
            match = pattern.search(text)
            if match:
                parsed = self._parse_decimal(match.group(1))
                if parsed is not None:
                    result[field] = self._decimal_to_str(parsed)

        # Extract Box 12 codes
        box12_codes: dict[str, str] = {}
        for match in self.BOX12_PATTERN.finditer(text):
            code = match.group(1).upper()
            parsed = self._parse_decimal(match.group(2))
            if parsed is not None:
                box12_codes[code] = self._decimal_to_str(parsed)
        if box12_codes:
            result["box12_codes"] = box12_codes

        # Extract Box 14 entries
        box14_other: dict[str, str] = {}
        for match in self.BOX14_ENTRY.finditer(text):
            label = match.group(1).upper()
            parsed = self._parse_decimal(match.group(2))
            if parsed is not None:
                box14_other[label] = self._decimal_to_str(parsed)
        if box14_other:
            result["box14_other"] = box14_other

        # Default state to CA
        result["state"] = "CA"

        return result

    # Valid IRS Box 12 codes (2024 W-2 instructions)
    VALID_BOX12_CODES = {
        "A", "B", "C", "D", "E", "F", "G", "H", "J", "K", "L", "M", "N",
        "P", "Q", "R", "S", "T", "V", "W", "Y", "Z",
        "AA", "BB", "DD", "EE", "FF", "GG", "HH",
    }

    # 2024 Social Security wage cap
    SS_WAGE_CAP = Decimal("168600")
    SS_TAX_RATE = Decimal("0.062")
    MEDICARE_TAX_RATE = Decimal("0.0145")

    def validate_extraction(self, data: dict[str, Any] | list[dict[str, Any]]) -> list[str]:
        """Validate W-2 extraction has required fields (hard errors only)."""
        if isinstance(data, list):
            data = data[0] if data else {}
        errors: list[str] = []
        required = ["tax_year", "employer_name", "box1_wages", "box2_federal_withheld"]
        for field in required:
            if field not in data or data[field] is None:
                errors.append(f"Missing required W-2 field: {field}")
        return errors

    def get_warnings(self, data: dict[str, Any] | list[dict[str, Any]]) -> list[str]:
        """Plausibility warnings for OCR error detection (non-blocking)."""
        if isinstance(data, list):
            data = data[0] if data else {}
        warnings: list[str] = []

        box1 = self._parse_decimal(data.get("box1_wages", "")) if data.get("box1_wages") else None
        box2 = self._parse_decimal(data.get("box2_federal_withheld", "")) if data.get("box2_federal_withheld") else None

        if box1 is not None and box2 is not None and box1 > 0:
            if box2 == box1:
                warnings.append(
                    f"Box 2 (${box2}) equals Box 1 (${box1}) — likely OCR duplication. "
                    "Federal withholding cannot be 100% of wages."
                )
            elif box2 > box1:
                warnings.append(
                    f"Box 2 (${box2}) exceeds Box 1 (${box1}) — federal withholding cannot exceed wages."
                )
            else:
                ratio = box2 / box1
                if ratio > Decimal("0.50"):
                    warnings.append(
                        f"Box 2/Box 1 ratio is {ratio:.1%} — unusually high withholding rate. "
                        "Verify these values are correct."
                    )

        box4 = self._parse_decimal(data.get("box4_ss_withheld", "")) if data.get("box4_ss_withheld") else None
        if box4 is not None:
            max_ss_tax = self.SS_WAGE_CAP * self.SS_TAX_RATE
            if box4 > max_ss_tax:
                warnings.append(
                    f"Box 4 (${box4}) exceeds maximum SS tax of ${max_ss_tax:.2f} "
                    f"(wage cap ${self.SS_WAGE_CAP} × {self.SS_TAX_RATE}). Likely OCR error."
                )

        box3 = self._parse_decimal(data.get("box3_ss_wages", "")) if data.get("box3_ss_wages") else None
        if box3 is not None and box3 > self.SS_WAGE_CAP:
            warnings.append(
                f"Box 3 (${box3}) exceeds SS wage cap of ${self.SS_WAGE_CAP}. "
                "Social Security wages are capped."
            )

        box5 = self._parse_decimal(data.get("box5_medicare_wages", "")) if data.get("box5_medicare_wages") else None
        box6 = self._parse_decimal(data.get("box6_medicare_withheld", "")) if data.get("box6_medicare_withheld") else None
        if box5 is not None and box6 is not None and box5 > 0:
            expected_medicare = box5 * self.MEDICARE_TAX_RATE
            if box6 > expected_medicare * Decimal("1.8"):
                warnings.append(
                    f"Box 6 (${box6}) seems too high relative to Box 5 (${box5}). "
                    f"Expected ~${expected_medicare:.2f} at base rate."
                )

        box12 = data.get("box12_codes", {})
        if box12:
            for code in box12:
                if code not in self.VALID_BOX12_CODES:
                    warnings.append(
                        f"Box 12 code '{code}' is not a recognized IRS code. "
                        f"Valid codes: {', '.join(sorted(self.VALID_BOX12_CODES))}. "
                        "This may be an OCR misread."
                    )

        return warnings
