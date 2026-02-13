"""Unit tests for the ManualAdapter."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.ingestion.manual import ManualAdapter
from app.models.enums import BrokerSource, EquityType, TransactionType
from app.models.tax_forms import (
    W2,
    Form1099B,
    Form1099DIV,
    Form1099INT,
    Form3921,
    Form3922,
)
from app.parsing.detector import FormType


@pytest.fixture
def adapter():
    return ManualAdapter()


@pytest.fixture
def w2_json():
    return {
        "tax_year": 2024,
        "employer_name": "Coinbase Inc",
        "employer_ein": None,
        "box1_wages": "614328.46",
        "box2_federal_withheld": "109772.46",
        "box3_ss_wages": "168600.00",
        "box4_ss_withheld": "10453.20",
        "box5_medicare_wages": "614328.46",
        "box6_medicare_withheld": "10854.49",
        "box12_codes": {"C": "405.08", "D": "12801.27", "DD": "8965.82"},
        "box14_other": {"RSU": "282417.52", "VPDI": "1760.00"},
        "box16_state_wages": "614328.46",
        "box17_state_withheld": "46460.39",
        "state": "CA",
    }


@pytest.fixture
def form_3921_json():
    return [
        {
            "tax_year": 2024,
            "corporation_name": "Acme Corp",
            "grant_date": "2022-01-15",
            "exercise_date": "2024-03-01",
            "exercise_price_per_share": "50.00",
            "fmv_on_exercise_date": "120.00",
            "shares_transferred": 200,
        }
    ]


@pytest.fixture
def form_3922_json():
    return [
        {
            "tax_year": 2024,
            "corporation_name": "Acme Corp",
            "offering_date": "2024-01-01",
            "purchase_date": "2024-06-30",
            "fmv_on_offering_date": "140.00",
            "fmv_on_purchase_date": "150.00",
            "purchase_price_per_share": "127.50",
            "shares_transferred": 50,
        }
    ]


@pytest.fixture
def form_1099b_json():
    return [
        {
            "tax_year": 2024,
            "broker_name": "Robinhood",
            "broker_source": "MANUAL",
            "description": "100 sh AAPL",
            "date_acquired": "2023-01-15",
            "date_sold": "2024-06-20",
            "proceeds": "15000.00",
            "cost_basis": "12000.00",
            "wash_sale_loss_disallowed": None,
            "basis_reported_to_irs": True,
        }
    ]


@pytest.fixture
def form_1099div_json():
    return {
        "tax_year": 2024,
        "payer_name": "Vanguard Total Stock Market",
        "ordinary_dividends": "1234.56",
        "qualified_dividends": "987.65",
        "capital_gain_distributions": "500.00",
        "federal_tax_withheld": "0.00",
    }


@pytest.fixture
def form_1099int_json():
    return {
        "tax_year": 2024,
        "payer_name": "Chase Bank",
        "interest_income": "456.78",
        "early_withdrawal_penalty": "0.00",
        "federal_tax_withheld": "0.00",
    }


class TestParseW2:
    def test_parse_w2_json(self, adapter, w2_json, tmp_path):
        f = tmp_path / "w2_2024.json"
        f.write_text(json.dumps(w2_json))
        result = adapter.parse(f)

        assert result.form_type == FormType.W2
        assert result.tax_year == 2024
        assert len(result.forms) == 1
        assert isinstance(result.forms[0], W2)
        assert result.forms[0].employer_name == "Coinbase Inc"
        assert result.events == []
        assert result.lots == []
        assert result.sales == []

    def test_parse_w2_decimal_conversion(self, adapter, w2_json, tmp_path):
        f = tmp_path / "w2_2024.json"
        f.write_text(json.dumps(w2_json))
        result = adapter.parse(f)
        w2 = result.forms[0]

        assert isinstance(w2.box1_wages, Decimal)
        assert w2.box1_wages == Decimal("614328.46")
        assert isinstance(w2.box2_federal_withheld, Decimal)
        assert w2.box2_federal_withheld == Decimal("109772.46")
        assert w2.box3_ss_wages == Decimal("168600.00")

    def test_parse_w2_box12_codes(self, adapter, w2_json, tmp_path):
        f = tmp_path / "w2_2024.json"
        f.write_text(json.dumps(w2_json))
        result = adapter.parse(f)
        w2 = result.forms[0]

        assert isinstance(w2.box12_codes, dict)
        assert w2.box12_codes["C"] == Decimal("405.08")
        assert w2.box12_codes["D"] == Decimal("12801.27")
        assert w2.box12_codes["DD"] == Decimal("8965.82")

    def test_parse_w2_box14_other(self, adapter, w2_json, tmp_path):
        f = tmp_path / "w2_2024.json"
        f.write_text(json.dumps(w2_json))
        result = adapter.parse(f)
        w2 = result.forms[0]

        assert w2.box14_other["RSU"] == Decimal("282417.52")
        assert w2.box14_other["VPDI"] == Decimal("1760.00")


class TestParse3921:
    def test_parse_3921_creates_event_and_lot(self, adapter, form_3921_json, tmp_path):
        f = tmp_path / "3921_2024.json"
        f.write_text(json.dumps(form_3921_json))
        result = adapter.parse(f)

        assert result.form_type == FormType.FORM_3921
        assert len(result.forms) == 1
        assert isinstance(result.forms[0], Form3921)

        assert len(result.events) == 1
        event = result.events[0]
        assert event.event_type == TransactionType.EXERCISE
        assert event.equity_type == EquityType.ISO
        assert event.shares == Decimal("200")
        assert event.strike_price == Decimal("50.00")
        assert event.price_per_share == Decimal("120.00")
        assert event.grant_date == date(2022, 1, 15)

        assert len(result.lots) == 1
        lot = result.lots[0]
        assert lot.equity_type == EquityType.ISO
        assert lot.shares == Decimal("200")
        assert lot.source_event_id == event.id

    def test_parse_3921_lot_has_amt_basis(self, adapter, form_3921_json, tmp_path):
        """ISO lot must have both regular and AMT basis per Form 3921 instructions."""
        f = tmp_path / "3921_2024.json"
        f.write_text(json.dumps(form_3921_json))
        result = adapter.parse(f)
        lot = result.lots[0]

        # Regular basis = strike price (Box 3)
        assert lot.cost_per_share == Decimal("50.00")
        # AMT basis = FMV on exercise date (Box 4)
        assert lot.amt_cost_per_share == Decimal("120.00")


class TestParse3922:
    def test_parse_3922_creates_event_and_lot(self, adapter, form_3922_json, tmp_path):
        f = tmp_path / "3922_2024.json"
        f.write_text(json.dumps(form_3922_json))
        result = adapter.parse(f)

        assert result.form_type == FormType.FORM_3922
        assert len(result.forms) == 1
        assert isinstance(result.forms[0], Form3922)

        assert len(result.events) == 1
        event = result.events[0]
        assert event.event_type == TransactionType.PURCHASE
        assert event.equity_type == EquityType.ESPP
        assert event.shares == Decimal("50")

        assert len(result.lots) == 1
        lot = result.lots[0]
        assert lot.equity_type == EquityType.ESPP
        assert lot.cost_per_share == Decimal("127.50")
        assert lot.amt_cost_per_share is None  # ESPP has no AMT at purchase

    def test_parse_3922_preserves_offering_date(self, adapter, form_3922_json, tmp_path):
        """offering_date must be preserved for qualifying disposition check."""
        f = tmp_path / "3922_2024.json"
        f.write_text(json.dumps(form_3922_json))
        result = adapter.parse(f)
        event = result.events[0]

        assert event.offering_date == date(2024, 1, 1)


class TestParse1099B:
    def test_parse_1099b_creates_sales(self, adapter, form_1099b_json, tmp_path):
        f = tmp_path / "1099b_2024.json"
        f.write_text(json.dumps(form_1099b_json))
        result = adapter.parse(f)

        assert result.form_type == FormType.FORM_1099B
        assert len(result.forms) == 1
        assert isinstance(result.forms[0], Form1099B)
        assert len(result.sales) == 1
        sale = result.sales[0]
        assert sale.lot_id == ""  # Not matched yet
        assert sale.broker_source == BrokerSource.MANUAL


class TestParse1099DIV:
    def test_parse_1099div(self, adapter, form_1099div_json, tmp_path):
        f = tmp_path / "1099div_2024.json"
        f.write_text(json.dumps(form_1099div_json))
        result = adapter.parse(f)

        assert result.form_type == FormType.FORM_1099DIV
        assert len(result.forms) == 1
        assert isinstance(result.forms[0], Form1099DIV)
        form = result.forms[0]
        assert form.ordinary_dividends == Decimal("1234.56")
        assert form.qualified_dividends == Decimal("987.65")


class TestParse1099INT:
    def test_parse_1099int(self, adapter, form_1099int_json, tmp_path):
        f = tmp_path / "1099int_2024.json"
        f.write_text(json.dumps(form_1099int_json))
        result = adapter.parse(f)

        assert result.form_type == FormType.FORM_1099INT
        assert len(result.forms) == 1
        assert isinstance(result.forms[0], Form1099INT)
        form = result.forms[0]
        assert form.interest_income == Decimal("456.78")


class TestValidation:
    def test_validate_w2_valid(self, adapter, w2_json, tmp_path):
        f = tmp_path / "w2.json"
        f.write_text(json.dumps(w2_json))
        result = adapter.parse(f)
        errors = adapter.validate(result)
        assert errors == []

    def test_validate_w2_missing_wages(self, adapter, tmp_path):
        data = {
            "tax_year": 2024,
            "employer_name": "Test Corp",
            "box1_wages": "0",
            "box2_federal_withheld": "0",
        }
        f = tmp_path / "w2.json"
        f.write_text(json.dumps(data))
        result = adapter.parse(f)
        errors = adapter.validate(result)
        assert any("box1_wages" in e for e in errors)

    def test_validate_3921_exercise_before_grant(self, adapter, tmp_path):
        data = [
            {
                "tax_year": 2024,
                "corporation_name": "Acme",
                "grant_date": "2024-06-01",
                "exercise_date": "2024-01-01",  # Before grant!
                "exercise_price_per_share": "50.00",
                "fmv_on_exercise_date": "120.00",
                "shares_transferred": 100,
            }
        ]
        f = tmp_path / "3921.json"
        f.write_text(json.dumps(data))
        result = adapter.parse(f)
        errors = adapter.validate(result)
        assert any("exercise_date must be after grant_date" in e for e in errors)

    def test_validate_1099div_qualified_exceeds_ordinary(self, adapter, tmp_path):
        data = {
            "tax_year": 2024,
            "payer_name": "Fund",
            "ordinary_dividends": "100.00",
            "qualified_dividends": "200.00",  # Can't exceed ordinary
        }
        f = tmp_path / "1099div.json"
        f.write_text(json.dumps(data))
        result = adapter.parse(f)
        errors = adapter.validate(result)
        assert any("ordinary_dividends" in e for e in errors)


class TestFormTypeDetection:
    def test_detect_form_type_from_json(self, adapter):
        assert adapter._detect_form_type({"box1_wages": "100"}) == FormType.W2
        assert adapter._detect_form_type({"interest_income": "50"}) == FormType.FORM_1099INT
        assert (
            adapter._detect_form_type({"ordinary_dividends": "100"})
            == FormType.FORM_1099DIV
        )
        assert (
            adapter._detect_form_type(
                [{"exercise_price_per_share": "50", "fmv_on_exercise_date": "120"}]
            )
            == FormType.FORM_3921
        )
        assert (
            adapter._detect_form_type(
                [{"purchase_price_per_share": "50", "fmv_on_purchase_date": "120"}]
            )
            == FormType.FORM_3922
        )
        assert (
            adapter._detect_form_type([{"proceeds": "100", "date_sold": "2024-01-01"}])
            == FormType.FORM_1099B
        )


class TestEdgeCases:
    def test_parse_file_not_found(self, adapter):
        with pytest.raises(FileNotFoundError):
            adapter.parse(Path("/nonexistent/file.json"))

    def test_parse_unknown_form_type(self, adapter, tmp_path):
        f = tmp_path / "mystery.json"
        f.write_text(json.dumps({"unknown_field": "value"}))
        with pytest.raises(ValueError, match="Cannot detect form type"):
            adapter.parse(f)
