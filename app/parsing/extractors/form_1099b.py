"""Form 1099-B (Broker Proceeds) PDF extractor."""

import re
from typing import Any

from app.parsing.base import BasePDFExtractor


class Form1099BExtractor(BasePDFExtractor):
    """Extracts Form 1099-B data from PDF text, primarily using tables."""

    BROKER_PATTERN = re.compile(
        r"(?:Payer|Broker|Filer)(?:'?s?)?\s*name[^\n]*?\n\s*(.+?)(?:\n|$)", re.IGNORECASE
    )

    # Common 1099-B table header variations
    HEADER_KEYWORDS = [
        "description", "date acquired", "date sold", "proceeds",
        "cost basis", "cost or other basis", "wash sale",
    ]

    def extract(self, text: str, tables: list[list[list[str]]] | None = None) -> list[dict[str, Any]]:
        """Extract 1099-B transaction records from PDF tables and text."""
        records: list[dict[str, Any]] = []

        # Tax year
        tax_year = None
        year_match = re.search(r"\b(20\d{2})\b", text)
        if year_match:
            tax_year = int(year_match.group(1))

        # Broker name
        broker_name = None
        broker_match = self.BROKER_PATTERN.search(text)
        if broker_match:
            broker_name = broker_match.group(1).strip()

        # Try table-based extraction first (preferred for 1099-B)
        if tables:
            records = self._extract_from_tables(tables, tax_year, broker_name)

        # Fallback to text-based extraction if no table records found
        if not records:
            records = self._extract_from_text(text, tax_year, broker_name)

        return records

    def _extract_from_tables(
        self, tables: list[list[list[str]]], tax_year: int | None, broker_name: str | None,
    ) -> list[dict[str, Any]]:
        """Extract records from pdfplumber table data."""
        records: list[dict[str, Any]] = []

        for table in tables:
            if not table or len(table) < 2:
                continue

            # Find header row
            header_idx = self._find_header_row(table)
            if header_idx is None:
                continue

            headers = [str(h).strip().lower() if h else "" for h in table[header_idx]]
            col_map = self._map_columns(headers)

            # Process data rows
            for row in table[header_idx + 1 :]:
                if not row or all(not cell or not str(cell).strip() for cell in row):
                    continue
                record = self._row_to_record(row, col_map, tax_year, broker_name)
                if record:
                    records.append(record)

        return records

    def _find_header_row(self, table: list[list[str]]) -> int | None:
        """Find the header row in a table by looking for known column names."""
        for i, row in enumerate(table):
            row_text = " ".join(str(cell).lower() for cell in row if cell)
            matches = sum(1 for kw in self.HEADER_KEYWORDS if kw in row_text)
            if matches >= 2:
                return i
        return None

    def _map_columns(self, headers: list[str]) -> dict[str, int]:
        """Map field names to column indices."""
        col_map: dict[str, int] = {}
        for i, header in enumerate(headers):
            if "description" in header or "property" in header:
                col_map["description"] = i
            elif "acquired" in header:
                col_map["date_acquired"] = i
            elif "sold" in header or "disposed" in header:
                col_map["date_sold"] = i
            elif "proceed" in header:
                col_map["proceeds"] = i
            elif "cost" in header or "basis" in header:
                col_map["cost_basis"] = i
            elif "wash" in header:
                col_map["wash_sale"] = i
            elif "reported" in header:
                col_map["basis_reported"] = i
            elif "type" in header or "term" in header:
                col_map["box_type"] = i
        return col_map

    def _row_to_record(
        self, row: list[str], col_map: dict[str, int], tax_year: int | None, broker_name: str | None,
    ) -> dict[str, Any] | None:
        """Convert a table row to a 1099-B record dict."""
        record: dict[str, Any] = {"broker_source": "MANUAL"}
        if tax_year:
            record["tax_year"] = tax_year
        if broker_name:
            record["broker_name"] = broker_name

        def get_cell(field: str) -> str | None:
            idx = col_map.get(field)
            if idx is not None and idx < len(row):
                val = str(row[idx]).strip() if row[idx] else None
                return val
            return None

        desc = get_cell("description")
        if not desc:
            return None
        record["description"] = desc

        date_acq = get_cell("date_acquired")
        if date_acq and date_acq.lower() != "various":
            record["date_acquired"] = self._parse_date(date_acq)
        elif date_acq:
            record["date_acquired"] = "Various"

        date_sold = get_cell("date_sold")
        if date_sold:
            record["date_sold"] = self._parse_date(date_sold)

        proceeds = get_cell("proceeds")
        if proceeds:
            parsed = self._parse_decimal(proceeds)
            if parsed is not None:
                record["proceeds"] = self._decimal_to_str(parsed)

        cost = get_cell("cost_basis")
        if cost:
            parsed = self._parse_decimal(cost)
            if parsed is not None:
                record["cost_basis"] = self._decimal_to_str(parsed)
        else:
            record["cost_basis"] = "0.00"

        wash = get_cell("wash_sale")
        if wash:
            parsed = self._parse_decimal(wash)
            if parsed is not None:
                record["wash_sale_loss_disallowed"] = self._decimal_to_str(parsed)

        reported = get_cell("basis_reported")
        if reported:
            record["basis_reported_to_irs"] = reported.lower() in ("yes", "y", "x", "true")
        else:
            record["basis_reported_to_irs"] = True

        return record

    def _extract_from_text(
        self, text: str, tax_year: int | None, broker_name: str | None,
    ) -> list[dict[str, Any]]:
        """Fallback: extract 1099-B records from raw text using regex."""
        records: list[dict[str, Any]] = []
        # Pattern for a typical 1099-B text entry
        entry_pattern = re.compile(
            r"(\d+\s*(?:sh|shares?)\s+\w+.*?)"
            r"(\d{1,2}/\d{1,2}/\d{2,4})\s+"
            r"(\d{1,2}/\d{1,2}/\d{2,4})\s+"
            r"\$?([\d,]+\.\d{2})\s+"
            r"\$?([\d,]+\.\d{2})",
            re.IGNORECASE,
        )
        for match in entry_pattern.finditer(text):
            record: dict[str, Any] = {"broker_source": "MANUAL"}
            if tax_year:
                record["tax_year"] = tax_year
            if broker_name:
                record["broker_name"] = broker_name
            record["description"] = match.group(1).strip()
            record["date_acquired"] = self._parse_date(match.group(2))
            record["date_sold"] = self._parse_date(match.group(3))
            parsed_proceeds = self._parse_decimal(match.group(4))
            if parsed_proceeds is not None:
                record["proceeds"] = self._decimal_to_str(parsed_proceeds)
            parsed_basis = self._parse_decimal(match.group(5))
            if parsed_basis is not None:
                record["cost_basis"] = self._decimal_to_str(parsed_basis)
            record["basis_reported_to_irs"] = True
            records.append(record)

        return records

    def validate_extraction(self, data: dict[str, Any] | list[dict[str, Any]]) -> list[str]:
        """Validate 1099-B extraction."""
        records = data if isinstance(data, list) else [data]
        errors: list[str] = []
        required = ["description", "date_sold", "proceeds"]
        for i, record in enumerate(records):
            for field in required:
                if field not in record or record[field] is None:
                    errors.append(f"Record {i + 1}: Missing required 1099-B field: {field}")
        return errors
