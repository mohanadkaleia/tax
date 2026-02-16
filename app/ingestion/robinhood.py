"""Robinhood adapter for consolidated 1099 CSV data."""

import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from app.ingestion.base import BaseAdapter, ImportResult
from app.models.enums import BrokerSource
from app.models.equity_event import Sale, Security
from app.models.tax_forms import Form1099B, Form1099DIV, Form1099INT
from app.parsing.detector import FormType


_BROKER_NAME = "Robinhood Markets Inc"

# Map well-known company names to tickers
_TICKER_MAP: dict[str, str] = {
    "COINBASE": "COIN",
    "STARBUCKS": "SBUX",
    "APPLE": "AAPL",
    "MICROSOFT": "MSFT",
    "GOOGLE": "GOOGL",
    "ALPHABET": "GOOGL",
    "AMAZON": "AMZN",
    "TESLA": "TSLA",
    "META": "META",
    "NVIDIA": "NVDA",
    "NETFLIX": "NFLX",
    "DISNEY": "DIS",
    "PAYPAL": "PYPL",
    "SHOPIFY": "SHOP",
    "SQUARE": "SQ",
    "BLOCK": "SQ",
    "AMD": "AMD",
    "INTEL": "INTC",
    "WALMART": "WMT",
    "TARGET": "TGT",
    "COSTCO": "COST",
    "BOEING": "BA",
    "FORD": "F",
    "GENERAL MOTORS": "GM",
    "COCA-COLA": "KO",
    "COCA COLA": "KO",
    "PEPSI": "PEP",
    "PEPSICO": "PEP",
    "JOHNSON": "JNJ",
    "PROCTER": "PG",
    "VISA": "V",
    "MASTERCARD": "MA",
    "JPMORGAN": "JPM",
    "BERKSHIRE": "BRK.B",
    "SALESFORCE": "CRM",
    "ADOBE": "ADBE",
    "UBER": "UBER",
    "LYFT": "LYFT",
    "AIRBNB": "ABNB",
    "PALANTIR": "PLTR",
    "SNOWFLAKE": "SNOW",
    "SOFI": "SOFI",
    "ROBINHOOD": "HOOD",
}

# Form type prefixes in the CSV
_SECTION_1099_DIV = "1099-DIV"
_SECTION_1099_INT = "1099-INT"
_SECTION_1099_B = "1099-B"
_KNOWN_SECTIONS = {_SECTION_1099_DIV, _SECTION_1099_INT, _SECTION_1099_B}


def _detect_ticker(description: str) -> str:
    """Detect stock ticker from a security description."""
    upper = description.upper()
    for keyword, ticker in _TICKER_MAP.items():
        if keyword in upper:
            return ticker
    return "UNKNOWN"


def _clean_description(description: str) -> str:
    """Clean up Robinhood descriptions that may have split words.

    For example: "STARBUCKS CORPORATION COMMON S TOCK"
    becomes:     "STARBUCKS CORPORATION COMMON STOCK"
    """
    # Fix common split patterns
    cleaned = description
    # Join single-letter fragments that look like split words
    # e.g., "S TOCK" -> "STOCK", "C LASS" -> "CLASS"
    parts = cleaned.split()
    merged: list[str] = []
    i = 0
    while i < len(parts):
        if (
            len(parts[i]) == 1
            and parts[i].isalpha()
            and i + 1 < len(parts)
            and parts[i + 1].isalpha()
            and parts[i + 1][0].islower()
        ):
            # Single letter followed by a lowercase-starting word -> merge
            merged.append(parts[i] + parts[i + 1])
            i += 2
        else:
            merged.append(parts[i])
            i += 1
    return " ".join(merged)


def _parse_date_yyyymmdd(value: str) -> date | None:
    """Parse a date string in YYYYMMDD format. Returns None for empty/blank."""
    stripped = value.strip()
    if not stripped:
        return None
    return datetime.strptime(stripped, "%Y%m%d").date()


def _decimal(value: str) -> Decimal:
    """Convert a string value to Decimal, defaulting to 0 for empty/invalid."""
    stripped = value.strip()
    if not stripped:
        return Decimal("0")
    try:
        return Decimal(stripped)
    except InvalidOperation:
        return Decimal("0")


def _decimal_or_none(value: str) -> Decimal | None:
    """Convert a string value to Decimal, returning None for empty/invalid."""
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return Decimal(stripped)
    except InvalidOperation:
        return None


def _basis_reported(form8949_code: str) -> bool:
    """Determine if basis is reported to IRS from Form 8949 code.

    A = short-term, basis reported
    B = short-term, basis NOT reported
    D = long-term, basis reported
    E = long-term, basis NOT reported
    """
    code = form8949_code.strip().upper()
    return code in ("A", "D")


class RobinhoodAdapter(BaseAdapter):
    """Adapter for parsing Robinhood consolidated 1099 CSV exports.

    The Robinhood CSV is a multi-section file containing 1099-DIV, 1099-INT,
    and 1099-B data. Each section starts with a header row where the first
    column is the form type, followed by data rows with the same prefix.
    """

    def parse(self, file_path: Path) -> ImportResult:
        """Parse Robinhood consolidated CSV into forms and sales."""
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        text = file_path.read_text(encoding="utf-8-sig")  # Handle BOM
        reader = csv.reader(text.splitlines())

        # Split rows into sections by form type
        sections: dict[str, tuple[list[str], list[list[str]]]] = {}
        current_section: str | None = None
        current_headers: list[str] = []

        for row in reader:
            if not row or not row[0].strip():
                continue

            form_prefix = row[0].strip()

            # Detect header row: first col is form type, second col is "ACCOUNT NUMBER"
            if form_prefix in _KNOWN_SECTIONS and len(row) > 1 and row[1].strip().upper() == "ACCOUNT NUMBER":
                current_section = form_prefix
                current_headers = [col.strip().upper() for col in row]
                if current_section not in sections:
                    sections[current_section] = (current_headers, [])
                else:
                    # Update headers if we see them again
                    sections[current_section] = (current_headers, sections[current_section][1])
                continue

            # Data row: first col matches a known section
            if form_prefix in _KNOWN_SECTIONS and current_section == form_prefix:
                if form_prefix in sections:
                    sections[form_prefix][1].append(row)

        forms: list[Form1099DIV | Form1099INT | Form1099B] = []
        sales: list[Sale] = []
        tax_year = 0

        # Parse 1099-DIV section
        if _SECTION_1099_DIV in sections:
            headers, rows = sections[_SECTION_1099_DIV]
            col_map = {name: idx for idx, name in enumerate(headers)}
            for row in rows:
                div_form, yr = self._parse_1099_div_row(row, col_map)
                forms.append(div_form)
                if yr:
                    tax_year = yr

        # Parse 1099-INT section
        if _SECTION_1099_INT in sections:
            headers, rows = sections[_SECTION_1099_INT]
            col_map = {name: idx for idx, name in enumerate(headers)}
            for row in rows:
                int_form, yr = self._parse_1099_int_row(row, col_map)
                forms.append(int_form)
                if yr:
                    tax_year = yr

        # Parse 1099-B section
        if _SECTION_1099_B in sections:
            headers, rows = sections[_SECTION_1099_B]
            col_map = {name: idx for idx, name in enumerate(headers)}
            for row in rows:
                b_form, sale, yr = self._parse_1099_b_row(row, col_map)
                forms.append(b_form)
                sales.append(sale)
                if yr:
                    tax_year = yr

        return ImportResult(
            form_type=FormType.ROBINHOOD_CONSOLIDATED,
            tax_year=tax_year,
            forms=forms,
            sales=sales,
        )

    def validate(self, data: ImportResult) -> list[str]:
        """Validate parsed Robinhood data for completeness and consistency."""
        errors: list[str] = []

        if not data.forms and not data.sales:
            errors.append("Robinhood CSV: No data parsed from file")
            return errors

        if data.tax_year == 0:
            errors.append("Robinhood CSV: Could not determine tax year")

        for i, form in enumerate(data.forms):
            if isinstance(form, Form1099B):
                if not form.description:
                    errors.append(f"1099-B record {i + 1}: description is missing")
                if form.proceeds <= 0:
                    errors.append(f"1099-B record {i + 1}: proceeds must be > 0")
            elif isinstance(form, Form1099DIV):
                if form.ordinary_dividends < form.qualified_dividends:
                    errors.append(
                        "1099-DIV: ordinary_dividends must be >= qualified_dividends"
                    )
            elif isinstance(form, Form1099INT):
                if form.interest_income < 0:
                    errors.append("1099-INT: interest_income must be >= 0")

        return errors

    # --- Section parsers ---

    @staticmethod
    def _get_col(row: list[str], col_map: dict[str, int], col_name: str, default: str = "") -> str:
        """Safely get a column value from a row by column name."""
        idx = col_map.get(col_name)
        if idx is None or idx >= len(row):
            return default
        return row[idx]

    def _parse_1099_div_row(
        self, row: list[str], col_map: dict[str, int]
    ) -> tuple[Form1099DIV, int]:
        """Parse a single 1099-DIV data row."""
        get = lambda name, default="": self._get_col(row, col_map, name, default)  # noqa: E731

        tax_year = int(get("TAX YEAR", "0"))

        # Extract payer name from CSV if available
        payer_name = get("PAYER NAME1", "").strip()
        if not payer_name:
            payer_name = _BROKER_NAME

        form = Form1099DIV(
            broker_name=payer_name,
            tax_year=tax_year,
            ordinary_dividends=_decimal(get("ORDINARY DIV")),
            qualified_dividends=_decimal(get("QUALIFIED DIV")),
            total_capital_gain_distributions=_decimal(get("TOTAL CAP GAIN")),
            nondividend_distributions=_decimal(get("NONTAXDIST")),
            section_199a_dividends=_decimal(get("SEC199A")),
            foreign_tax_paid=_decimal(get("FORTAXPD")),
            foreign_country=get("FORCNT").strip() or None,
            federal_tax_withheld=_decimal(get("FEDTAXWH")),
            state_tax_withheld=_decimal(get("STATETAXWHELD")),
        )
        return form, tax_year

    def _parse_1099_int_row(
        self, row: list[str], col_map: dict[str, int]
    ) -> tuple[Form1099INT, int]:
        """Parse a single 1099-INT data row."""
        get = lambda name, default="": self._get_col(row, col_map, name, default)  # noqa: E731

        tax_year = int(get("TAX YEAR", "0"))

        payer_name = get("PAYER NAME1", "").strip()
        if not payer_name:
            payer_name = _BROKER_NAME

        form = Form1099INT(
            payer_name=payer_name,
            tax_year=tax_year,
            interest_income=_decimal(get("INT INCOME")),
            early_withdrawal_penalty=_decimal(get("EARLY WD PENALTY")),
            us_savings_bond_interest=_decimal(get("INT USBONDS")),
            federal_tax_withheld=_decimal(get("FED TAX WH")),
            state_tax_withheld=_decimal(get("STATETAXWHELD")),
        )
        return form, tax_year

    def _parse_1099_b_row(
        self, row: list[str], col_map: dict[str, int]
    ) -> tuple[Form1099B, Sale, int]:
        """Parse a single 1099-B data row into a Form1099B and a Sale."""
        get = lambda name, default="": self._get_col(row, col_map, name, default)  # noqa: E731

        tax_year = int(get("TAX YEAR", "0"))

        # Parse description — clean up split words
        raw_description = get("DESCRIPTION")
        description = _clean_description(raw_description)

        # Parse dates
        date_acquired = _parse_date_yyyymmdd(get("DATE ACQUIRED"))
        date_sold_str = get("SALE DATE")
        date_sold = _parse_date_yyyymmdd(date_sold_str)
        if date_sold is None:
            raise ValueError(
                f"1099-B row missing SALE DATE: {description}"
            )

        # Parse amounts
        proceeds = _decimal(get("SALES PRICE"))
        cost_basis = _decimal_or_none(get("COST BASIS"))
        shares = _decimal(get("SHARES"))
        wash_sale = _decimal_or_none(get("WASH AMT DISALLOWED"))

        # Form 8949 code and basis reporting
        form8949_code = get("FORM8949CODE").strip().upper()
        basis_reported = _basis_reported(form8949_code)

        # Also check NON COVERED and BASIS NOT SHOWN flags
        non_covered = get("NON COVERED").strip().upper()
        basis_not_shown = get("BASIS NOT SHOWN").strip().upper()
        if non_covered == "Y" or basis_not_shown == "Y":
            basis_reported = False

        form = Form1099B(
            broker_name=_BROKER_NAME,
            tax_year=tax_year,
            description=description,
            date_acquired=date_acquired,
            date_sold=date_sold,
            proceeds=proceeds,
            cost_basis=cost_basis,
            wash_sale_loss_disallowed=wash_sale,
            basis_reported_to_irs=basis_reported,
            box_type=form8949_code or None,
            broker_source=BrokerSource.ROBINHOOD,
        )

        # Compute proceeds per share
        if shares > 0:
            proceeds_per_share = proceeds / shares
        else:
            proceeds_per_share = proceeds  # Fallback: store total

        # Detect ticker from description
        ticker = _detect_ticker(description)

        sale = Sale(
            id=str(uuid4()),
            lot_id="",  # Not matched yet — happens at reconcile
            security=Security(ticker=ticker, name=description),
            date_acquired=date_acquired if date_acquired else "Various",
            sale_date=date_sold,
            shares=shares,
            proceeds_per_share=proceeds_per_share,
            broker_reported_basis=cost_basis,
            wash_sale_disallowed=wash_sale if wash_sale else Decimal("0"),
            basis_reported_to_irs=basis_reported,
            broker_source=BrokerSource.ROBINHOOD,
        )

        return form, sale, tax_year
