"""Unit tests for the RobinhoodAdapter."""

import textwrap
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.ingestion.robinhood import (
    RobinhoodAdapter,
    _basis_reported,
    _clean_description,
    _detect_ticker,
    _parse_date_yyyymmdd,
)
from app.models.enums import BrokerSource
from app.models.tax_forms import Form1099B, Form1099DIV, Form1099INT
from app.parsing.detector import FormType


@pytest.fixture
def adapter():
    return RobinhoodAdapter()


# ---------------------------------------------------------------------------
# CSV fixtures
# ---------------------------------------------------------------------------

_DIV_HEADER = (
    "1099-DIV,ACCOUNT NUMBER,TAX YEAR,ORDINARY DIV,QUALIFIED DIV,"
    "TOTAL CAP GAIN,UNRECSEC1250,SEC1202,P28 GAIN,SEC897DIV,SEC897GAIN,"
    "NONTAXDIST,FEDTAXWH,SEC199A,INVESTEXP,FORTAXPD,FORCNT,CASHLIQ,"
    "NONCASHLIQ,EXEMPTINTDIV,SPECIFIEDPABINTDIV,FATCA,STATECODE,"
    "STATEIDNUM,STATETAXWHELD,PAYER FED ID,PAYER NAME1"
)
_DIV_ROW = (
    "1099-DIV,746590553,2024,405.83,392.12,0,0,0,0,0,0,"
    "2.88,0,13.71,0,12.21,,0,0,0,0,,,,,464364776,Robinhood Markets Inc as agent"
)

_INT_HEADER = (
    "1099-INT,ACCOUNT NUMBER,TAX YEAR,PAYER RTN,INT INCOME,"
    "EARLY WD PENALTY,INT USBONDS,FED TAX WH,INV EXPENSE,FOR TAX PD,"
    "FORCNT,TAX EXPMT INT,SPECIFIEDPABINT,MARKETDISCOUNT,BONDPREMIUM,"
    "BONDPREMIUMTREASURY,BONDPREMIUMTAXEXEMPT,CUSIP,FATCA,STATECODE,"
    "STATEIDNUM,STATETAXWHELD,PAYER FED ID,PAYER NAME1"
)
_INT_ROW = (
    "1099-INT,746590553,2024,,43.75,0,0,0,0,0,,0,0,0,0,0,0,,,,,,"
    "464364776,Robinhood Markets Inc as agent"
)

_1099B_HEADER = (
    "1099-B,ACCOUNT NUMBER,TAX YEAR,DATE ACQUIRED,SALE DATE,DESCRIPTION,"
    "SHARES,COST BASIS,SALES PRICE,TERM,ORDINARY,FED TAX WITHHELD,"
    "WASH AMT DISALLOWED,ACCRDMKTDISCOUNT,FORM8949CODE,"
    "GROSSPROCEEDSINDICATOR,LOSSNOTALLOWED,NON COVERED,BASIS NOT SHOWN,"
    "FORM 1099 NOT REC,COLLECTIBLE,QOF,PROFIT,UNRELPROFITPREV,"
    "UNRELPROFIT,AGGPROFIT"
)
_1099B_ROW_SHORT = (
    "1099-B,746590553,2024,,20240813,STARBUCKS CORPORATION COMMON S TOCK,"
    "4.23697,400.00,391.33,SHORT,N,,0,,A,,,,,,N,N,,,,"
)
_1099B_ROW_LONG = (
    "1099-B,746590553,2024,20230101,20240813,STARBUCKS CORPORATION COMMON S TOCK,"
    "7.76922,800.00,717.58,LONG,N,,0,,D,,,,,,N,N,,,,"
)
_1099B_ROW_WASH = (
    "1099-B,746590553,2024,,20240915,TESLA INC COMMON STOCK,"
    "2.5,250.00,230.00,SHORT,N,,15.50,,A,,,,,,N,N,,,,"
)
_1099B_ROW_NOT_REPORTED = (
    "1099-B,746590553,2024,,20240601,APPLE INC COMMON STOCK,"
    "10,1500.00,1600.00,SHORT,N,,0,,B,,,Y,,,N,N,,,,"
)


def _make_csv(*rows: str) -> str:
    """Join rows into a CSV string."""
    return "\n".join(rows)


def _write_csv(tmp_path: Path, *rows: str) -> Path:
    """Write rows to a temp CSV file and return the path."""
    csv_path = tmp_path / "robinhood.csv"
    csv_path.write_text(_make_csv(*rows))
    return csv_path


# ---------------------------------------------------------------------------
# 1099-DIV tests
# ---------------------------------------------------------------------------


class TestParse1099DIV:
    def test_parse_1099_div(self, adapter, tmp_path):
        """Parse 1099-DIV section from Robinhood CSV."""
        csv_path = _write_csv(tmp_path, _DIV_HEADER, _DIV_ROW)
        result = adapter.parse(csv_path)

        assert result.tax_year == 2024
        div_forms = [f for f in result.forms if isinstance(f, Form1099DIV)]
        assert len(div_forms) == 1

        div = div_forms[0]
        assert div.ordinary_dividends == Decimal("405.83")
        assert div.qualified_dividends == Decimal("392.12")
        assert div.total_capital_gain_distributions == Decimal("0")
        assert div.nondividend_distributions == Decimal("2.88")
        assert div.section_199a_dividends == Decimal("13.71")
        assert div.foreign_tax_paid == Decimal("12.21")
        assert div.federal_tax_withheld == Decimal("0")
        assert div.tax_year == 2024
        assert "Robinhood" in div.broker_name


# ---------------------------------------------------------------------------
# 1099-INT tests
# ---------------------------------------------------------------------------


class TestParse1099INT:
    def test_parse_1099_int(self, adapter, tmp_path):
        """Parse 1099-INT section from Robinhood CSV."""
        csv_path = _write_csv(tmp_path, _INT_HEADER, _INT_ROW)
        result = adapter.parse(csv_path)

        assert result.tax_year == 2024
        int_forms = [f for f in result.forms if isinstance(f, Form1099INT)]
        assert len(int_forms) == 1

        intform = int_forms[0]
        assert intform.interest_income == Decimal("43.75")
        assert intform.early_withdrawal_penalty == Decimal("0")
        assert intform.us_savings_bond_interest == Decimal("0")
        assert intform.federal_tax_withheld == Decimal("0")
        assert intform.tax_year == 2024
        assert "Robinhood" in intform.payer_name


# ---------------------------------------------------------------------------
# 1099-B tests
# ---------------------------------------------------------------------------


class TestParse1099B:
    def test_parse_1099_b_single_sale(self, adapter, tmp_path):
        """Parse single 1099-B sale from Robinhood CSV."""
        csv_path = _write_csv(tmp_path, _1099B_HEADER, _1099B_ROW_SHORT)
        result = adapter.parse(csv_path)

        assert result.tax_year == 2024
        b_forms = [f for f in result.forms if isinstance(f, Form1099B)]
        assert len(b_forms) == 1
        assert len(result.sales) == 1

        form = b_forms[0]
        assert form.proceeds == Decimal("391.33")
        assert form.cost_basis == Decimal("400.00")
        assert form.tax_year == 2024
        assert form.broker_source == BrokerSource.ROBINHOOD

    def test_parse_1099_b_multiple_sales(self, adapter, tmp_path):
        """Parse multiple 1099-B sales from a single CSV."""
        csv_path = _write_csv(
            tmp_path, _1099B_HEADER, _1099B_ROW_SHORT, _1099B_ROW_LONG
        )
        result = adapter.parse(csv_path)

        b_forms = [f for f in result.forms if isinstance(f, Form1099B)]
        assert len(b_forms) == 2
        assert len(result.sales) == 2

        # First sale: short-term, no date acquired
        assert b_forms[0].date_acquired is None
        assert b_forms[0].proceeds == Decimal("391.33")
        assert b_forms[0].box_type == "A"

        # Second sale: long-term, with date acquired
        assert b_forms[1].date_acquired == date(2023, 1, 1)
        assert b_forms[1].proceeds == Decimal("717.58")
        assert b_forms[1].box_type == "D"

    def test_fractional_shares(self, adapter, tmp_path):
        """Robinhood allows fractional shares like 4.23697."""
        csv_path = _write_csv(tmp_path, _1099B_HEADER, _1099B_ROW_SHORT)
        result = adapter.parse(csv_path)

        sale = result.sales[0]
        assert sale.shares == Decimal("4.23697")
        # Verify proceeds_per_share is computed correctly
        expected_pps = Decimal("391.33") / Decimal("4.23697")
        assert sale.proceeds_per_share == expected_pps

    def test_wash_sale_amounts(self, adapter, tmp_path):
        """Wash sale disallowed amount parsed correctly."""
        csv_path = _write_csv(tmp_path, _1099B_HEADER, _1099B_ROW_WASH)
        result = adapter.parse(csv_path)

        b_forms = [f for f in result.forms if isinstance(f, Form1099B)]
        assert b_forms[0].wash_sale_loss_disallowed == Decimal("15.50")

        sale = result.sales[0]
        assert sale.wash_sale_disallowed == Decimal("15.50")


# ---------------------------------------------------------------------------
# Date parsing tests
# ---------------------------------------------------------------------------


class TestDateParsing:
    def test_date_parsing_yyyymmdd(self, adapter, tmp_path):
        """Robinhood dates in YYYYMMDD format."""
        csv_path = _write_csv(tmp_path, _1099B_HEADER, _1099B_ROW_LONG)
        result = adapter.parse(csv_path)

        form = [f for f in result.forms if isinstance(f, Form1099B)][0]
        assert form.date_acquired == date(2023, 1, 1)
        assert form.date_sold == date(2024, 8, 13)

    def test_empty_date_acquired_means_various(self, adapter, tmp_path):
        """Empty date acquired = 'Various' in Sale model."""
        csv_path = _write_csv(tmp_path, _1099B_HEADER, _1099B_ROW_SHORT)
        result = adapter.parse(csv_path)

        form = [f for f in result.forms if isinstance(f, Form1099B)][0]
        assert form.date_acquired is None

        sale = result.sales[0]
        assert sale.date_acquired == "Various"

    def test_parse_date_yyyymmdd_function(self):
        """Direct test of the date parsing helper."""
        assert _parse_date_yyyymmdd("20240813") == date(2024, 8, 13)
        assert _parse_date_yyyymmdd("20230101") == date(2023, 1, 1)
        assert _parse_date_yyyymmdd("") is None
        assert _parse_date_yyyymmdd("  ") is None


# ---------------------------------------------------------------------------
# Form 8949 code mapping tests
# ---------------------------------------------------------------------------


class TestForm8949CodeMapping:
    def test_form8949_code_a_basis_reported(self, adapter, tmp_path):
        """Code A = short-term, basis reported to IRS."""
        csv_path = _write_csv(tmp_path, _1099B_HEADER, _1099B_ROW_SHORT)
        result = adapter.parse(csv_path)

        form = [f for f in result.forms if isinstance(f, Form1099B)][0]
        assert form.basis_reported_to_irs is True
        assert form.box_type == "A"

    def test_form8949_code_d_basis_reported(self, adapter, tmp_path):
        """Code D = long-term, basis reported to IRS."""
        csv_path = _write_csv(tmp_path, _1099B_HEADER, _1099B_ROW_LONG)
        result = adapter.parse(csv_path)

        form = [f for f in result.forms if isinstance(f, Form1099B)][0]
        assert form.basis_reported_to_irs is True
        assert form.box_type == "D"

    def test_form8949_code_b_basis_not_reported(self, adapter, tmp_path):
        """Code B = short-term, basis NOT reported to IRS."""
        csv_path = _write_csv(tmp_path, _1099B_HEADER, _1099B_ROW_NOT_REPORTED)
        result = adapter.parse(csv_path)

        form = [f for f in result.forms if isinstance(f, Form1099B)][0]
        assert form.basis_reported_to_irs is False
        assert form.box_type == "B"

    def test_basis_reported_helper(self):
        """Direct test of the _basis_reported helper."""
        assert _basis_reported("A") is True
        assert _basis_reported("D") is True
        assert _basis_reported("B") is False
        assert _basis_reported("E") is False
        assert _basis_reported("") is False


# ---------------------------------------------------------------------------
# Full consolidated CSV test
# ---------------------------------------------------------------------------


class TestFullConsolidatedCSV:
    def test_parse_full_consolidated(self, adapter, tmp_path):
        """Parse complete consolidated CSV with all sections."""
        csv_path = _write_csv(
            tmp_path,
            _DIV_HEADER,
            _DIV_ROW,
            _INT_HEADER,
            _INT_ROW,
            _1099B_HEADER,
            _1099B_ROW_SHORT,
            _1099B_ROW_LONG,
        )
        result = adapter.parse(csv_path)

        assert result.form_type == FormType.ROBINHOOD_CONSOLIDATED
        assert result.tax_year == 2024

        div_forms = [f for f in result.forms if isinstance(f, Form1099DIV)]
        int_forms = [f for f in result.forms if isinstance(f, Form1099INT)]
        b_forms = [f for f in result.forms if isinstance(f, Form1099B)]

        assert len(div_forms) == 1
        assert len(int_forms) == 1
        assert len(b_forms) == 2
        assert len(result.sales) == 2


# ---------------------------------------------------------------------------
# Ticker extraction tests
# ---------------------------------------------------------------------------


class TestTickerExtraction:
    def test_ticker_extraction_starbucks(self):
        """Extract ticker from STARBUCKS description."""
        assert _detect_ticker("STARBUCKS CORPORATION COMMON STOCK") == "SBUX"

    def test_ticker_extraction_tesla(self):
        """Extract ticker from TESLA description."""
        assert _detect_ticker("TESLA INC COMMON STOCK") == "TSLA"

    def test_ticker_extraction_apple(self):
        """Extract ticker from APPLE description."""
        assert _detect_ticker("APPLE INC COMMON STOCK") == "AAPL"

    def test_ticker_extraction_unknown(self):
        """Unknown company returns UNKNOWN."""
        assert _detect_ticker("ACME WIDGETS LLC COMMON STOCK") == "UNKNOWN"

    def test_ticker_in_sale_object(self, adapter, tmp_path):
        """Ticker is correctly set in Sale security."""
        csv_path = _write_csv(tmp_path, _1099B_HEADER, _1099B_ROW_SHORT)
        result = adapter.parse(csv_path)

        sale = result.sales[0]
        assert sale.security.ticker == "SBUX"


# ---------------------------------------------------------------------------
# Description cleaning tests
# ---------------------------------------------------------------------------


class TestDescriptionCleaning:
    def test_clean_split_word(self):
        """Clean split words like 'S TOCK' -> 'STOCK'."""
        # The function merges single-letter + lowercase-starting word
        assert _clean_description("COMMON S tock") == "COMMON Stock"

    def test_no_split(self):
        """Description without splits is unchanged."""
        assert _clean_description("APPLE INC COMMON STOCK") == "APPLE INC COMMON STOCK"


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


class TestValidation:
    def test_validation_no_errors(self, adapter, tmp_path):
        """Valid data produces no validation errors."""
        csv_path = _write_csv(
            tmp_path, _DIV_HEADER, _DIV_ROW, _1099B_HEADER, _1099B_ROW_LONG
        )
        result = adapter.parse(csv_path)
        errors = adapter.validate(result)
        assert errors == []

    def test_validation_empty_csv(self, adapter, tmp_path):
        """Empty CSV produces validation error."""
        csv_path = _write_csv(tmp_path, "")
        result = adapter.parse(csv_path)
        errors = adapter.validate(result)
        assert any("No data parsed" in e for e in errors)

    def test_validation_div_qualified_exceeds_ordinary(self, adapter, tmp_path):
        """Validation catches qualified > ordinary dividends."""
        # Manually construct invalid DIV row with qualified > ordinary
        bad_div_row = (
            "1099-DIV,746590553,2024,100.00,200.00,0,0,0,0,0,0,"
            "0,0,0,0,0,,0,0,0,0,,,,,464364776,Robinhood"
        )
        csv_path = _write_csv(tmp_path, _DIV_HEADER, bad_div_row)
        result = adapter.parse(csv_path)
        errors = adapter.validate(result)
        assert any("ordinary_dividends must be >= qualified_dividends" in e for e in errors)

    def test_validation_zero_proceeds(self, adapter, tmp_path):
        """Validation catches zero proceeds in 1099-B."""
        bad_b_row = (
            "1099-B,746590553,2024,,20240813,TEST CORP,"
            "1,100.00,0,SHORT,N,,0,,A,,,,,,N,N,,,,"
        )
        csv_path = _write_csv(tmp_path, _1099B_HEADER, bad_b_row)
        result = adapter.parse(csv_path)
        errors = adapter.validate(result)
        assert any("proceeds must be > 0" in e for e in errors)

    def test_validation_missing_description(self, adapter, tmp_path):
        """Validation catches missing description in 1099-B."""
        bad_b_row = (
            "1099-B,746590553,2024,,20240813,,"
            "1,100.00,50.00,SHORT,N,,0,,A,,,,,,N,N,,,,"
        )
        csv_path = _write_csv(tmp_path, _1099B_HEADER, bad_b_row)
        result = adapter.parse(csv_path)
        errors = adapter.validate(result)
        assert any("description is missing" in e for e in errors)


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_file_not_found(self, adapter):
        """FileNotFoundError raised for missing file."""
        with pytest.raises(FileNotFoundError):
            adapter.parse(Path("/nonexistent/robinhood.csv"))

    def test_broker_source_is_robinhood(self, adapter, tmp_path):
        """Verify broker_source is set to ROBINHOOD for all sales and forms."""
        csv_path = _write_csv(tmp_path, _1099B_HEADER, _1099B_ROW_SHORT)
        result = adapter.parse(csv_path)

        for form in result.forms:
            if isinstance(form, Form1099B):
                assert form.broker_source == BrokerSource.ROBINHOOD

        for sale in result.sales:
            assert sale.broker_source == BrokerSource.ROBINHOOD

    def test_sale_lot_id_empty(self, adapter, tmp_path):
        """Sale.lot_id should be empty (matched later at reconcile)."""
        csv_path = _write_csv(tmp_path, _1099B_HEADER, _1099B_ROW_SHORT)
        result = adapter.parse(csv_path)

        for sale in result.sales:
            assert sale.lot_id == ""

    def test_only_div_section(self, adapter, tmp_path):
        """CSV with only 1099-DIV section produces no sales."""
        csv_path = _write_csv(tmp_path, _DIV_HEADER, _DIV_ROW)
        result = adapter.parse(csv_path)

        assert len(result.sales) == 0
        div_forms = [f for f in result.forms if isinstance(f, Form1099DIV)]
        assert len(div_forms) == 1

    def test_only_int_section(self, adapter, tmp_path):
        """CSV with only 1099-INT section produces no sales."""
        csv_path = _write_csv(tmp_path, _INT_HEADER, _INT_ROW)
        result = adapter.parse(csv_path)

        assert len(result.sales) == 0
        int_forms = [f for f in result.forms if isinstance(f, Form1099INT)]
        assert len(int_forms) == 1

    def test_date_acquired_present_in_sale(self, adapter, tmp_path):
        """When date_acquired is present, Sale has a proper date object."""
        csv_path = _write_csv(tmp_path, _1099B_HEADER, _1099B_ROW_LONG)
        result = adapter.parse(csv_path)

        sale = result.sales[0]
        assert sale.date_acquired == date(2023, 1, 1)

    def test_non_covered_overrides_basis_reported(self, adapter, tmp_path):
        """NON COVERED = Y forces basis_reported_to_irs to False."""
        csv_path = _write_csv(tmp_path, _1099B_HEADER, _1099B_ROW_NOT_REPORTED)
        result = adapter.parse(csv_path)

        sale = result.sales[0]
        assert sale.basis_reported_to_irs is False
