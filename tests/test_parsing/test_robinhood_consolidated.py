"""Tests for Robinhood consolidated 1099 PDF extractor."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.ingestion.manual import ManualAdapter
from app.models.tax_forms import Form1099B, Form1099DIV, Form1099INT
from app.parsing.detector import FormType, detect_form_type
from app.parsing.extractors.robinhood_consolidated import RobinhoodConsolidatedExtractor


# --- Sample text matching Robinhood's consolidated PDF format ---

ROBINHOOD_SUMMARY_TEXT = """\
Robinhood Securities LLC
2025 Tax Documents
Summary Information

Form 1099-DIV: Dividends and Distributions
1a- Total ordinary dividends                   3,475.63
1b- Qualified dividends                        2,891.45
2a- Total capital gain distributions              150.00
3-  Nondividend distributions                       5.12
5-  Section 199A dividends                         42.30
7-  Foreign tax paid                               18.50
4-  Federal income tax withheld                     0.00

Form 1099-INT: Interest Income
1-  Interest income                               610.38
4-  Federal income tax withheld                     0.00

Form 1099-B: Proceeds From Broker
No transactions this year.
"""

ROBINHOOD_WITH_1099B_TEXT = """\
Robinhood Securities LLC
2025 Tax Documents
Summary Information

Form 1099-DIV: Dividends and Distributions
1a- Total ordinary dividends                      100.00
1b- Qualified dividends                            80.00

Form 1099-INT: Interest Income
1-  Interest income                                50.00

Form 1099-B: Proceeds From Broker
100 shares AAPL 01/15/2024 06/01/2025 $17,500.00 $15,000.00
"""


class TestDetection:
    def test_detect_robinhood_consolidated(self):
        assert detect_form_type(ROBINHOOD_SUMMARY_TEXT) == FormType.ROBINHOOD_CONSOLIDATED

    def test_detect_robinhood_before_individual_1099(self):
        """Consolidated should match before individual 1099-B."""
        text = "Robinhood Securities LLC Summary Information Form 1099-B Proceeds From Broker"
        assert detect_form_type(text) == FormType.ROBINHOOD_CONSOLIDATED

    def test_non_robinhood_1099b_still_detects(self):
        """A non-Robinhood 1099-B should still be detected normally."""
        text = "Form 1099-B Proceeds From Broker and Barter Exchange Transactions"
        assert detect_form_type(text) == FormType.FORM_1099B


class TestRobinhoodConsolidatedExtractor:
    def setup_method(self):
        self.extractor = RobinhoodConsolidatedExtractor()

    def test_extract_div_and_int(self):
        result = self.extractor.extract(ROBINHOOD_SUMMARY_TEXT)
        assert result["consolidated"] is True
        assert result["payer_name"] == "Robinhood Securities LLC"
        assert result["tax_year"] == 2025

        div = result["form_1099div"]
        assert div["ordinary_dividends"] == "3475.63"
        assert div["qualified_dividends"] == "2891.45"
        assert div["capital_gain_distributions"] == "150.00"
        assert div["nondividend_distributions"] == "5.12"
        assert div["section_199a_dividends"] == "42.30"
        assert div["foreign_tax_paid"] == "18.50"
        assert div["federal_tax_withheld"] == "0.00"

        int_data = result["form_1099int"]
        assert int_data["interest_income"] == "610.38"
        assert int_data["federal_tax_withheld"] == "0.00"

    def test_no_1099b_when_no_transactions(self):
        result = self.extractor.extract(ROBINHOOD_SUMMARY_TEXT)
        assert "form_1099b" not in result

    def test_extract_with_1099b(self):
        result = self.extractor.extract(ROBINHOOD_WITH_1099B_TEXT)
        assert "form_1099b" in result
        assert len(result["form_1099b"]) == 1
        assert result["form_1099b"][0]["proceeds"] == "17500.00"

    def test_extract_div_only(self):
        text = """\
Robinhood Securities LLC
2025 Summary Information

Form 1099-DIV: Dividends and Distributions
1a- Total ordinary dividends                      500.00
1b- Qualified dividends                           300.00
"""
        result = self.extractor.extract(text)
        assert "form_1099div" in result
        assert result["form_1099div"]["ordinary_dividends"] == "500.00"
        assert "form_1099int" not in result

    def test_extract_int_only(self):
        text = """\
Robinhood Securities LLC
2025 Summary Information

Form 1099-INT: Interest Income
1-  Interest income                               200.00
"""
        result = self.extractor.extract(text)
        assert "form_1099int" in result
        assert result["form_1099int"]["interest_income"] == "200.00"
        assert "form_1099div" not in result

    def test_validate_extraction_valid(self):
        result = self.extractor.extract(ROBINHOOD_SUMMARY_TEXT)
        errors = self.extractor.validate_extraction(result)
        assert errors == []

    def test_validate_extraction_no_data(self):
        result = {"consolidated": True, "payer_name": "Robinhood Securities LLC"}
        errors = self.extractor.validate_extraction(result)
        assert len(errors) == 1
        assert "no sub-form data" in errors[0]

    def test_tax_year_propagated_to_subforms(self):
        result = self.extractor.extract(ROBINHOOD_SUMMARY_TEXT)
        assert result["form_1099div"]["tax_year"] == 2025
        assert result["form_1099int"]["tax_year"] == 2025


class TestManualAdapterConsolidated:
    """Tests for ManualAdapter handling of consolidated JSON."""

    @pytest.fixture
    def adapter(self):
        return ManualAdapter()

    @pytest.fixture
    def consolidated_json(self):
        return {
            "consolidated": True,
            "payer_name": "Robinhood Securities LLC",
            "tax_year": 2025,
            "form_1099div": {
                "tax_year": 2025,
                "payer_name": "Robinhood Securities LLC",
                "ordinary_dividends": "3475.63",
                "qualified_dividends": "2891.45",
                "capital_gain_distributions": "150.00",
                "nondividend_distributions": "5.12",
                "section_199a_dividends": "42.30",
                "foreign_tax_paid": "18.50",
                "federal_tax_withheld": "0.00",
            },
            "form_1099int": {
                "tax_year": 2025,
                "payer_name": "Robinhood Securities LLC",
                "interest_income": "610.38",
                "federal_tax_withheld": "0.00",
            },
        }

    def test_detect_consolidated_form_type(self, adapter):
        data = {"consolidated": True, "payer_name": "Robinhood Securities LLC"}
        assert adapter._detect_form_type(data) == FormType.ROBINHOOD_CONSOLIDATED

    def test_parse_consolidated_div_and_int(self, adapter, consolidated_json, tmp_path):
        f = tmp_path / "robinhood_2025.json"
        f.write_text(json.dumps(consolidated_json))
        result = adapter.parse(f)

        assert result.form_type == FormType.ROBINHOOD_CONSOLIDATED
        assert result.tax_year == 2025

        div_forms = [f for f in result.forms if isinstance(f, Form1099DIV)]
        assert len(div_forms) == 1
        assert div_forms[0].ordinary_dividends == Decimal("3475.63")
        assert div_forms[0].qualified_dividends == Decimal("2891.45")
        assert div_forms[0].nondividend_distributions == Decimal("5.12")
        assert div_forms[0].section_199a_dividends == Decimal("42.30")
        assert div_forms[0].foreign_tax_paid == Decimal("18.50")

        int_forms = [f for f in result.forms if isinstance(f, Form1099INT)]
        assert len(int_forms) == 1
        assert int_forms[0].interest_income == Decimal("610.38")

    def test_parse_consolidated_with_1099b(self, adapter, consolidated_json, tmp_path):
        consolidated_json["form_1099b"] = [
            {
                "tax_year": 2025,
                "broker_name": "Robinhood",
                "broker_source": "MANUAL",
                "description": "50 sh AAPL",
                "date_acquired": "2024-01-15",
                "date_sold": "2025-06-20",
                "proceeds": "9000.00",
                "cost_basis": "7500.00",
                "basis_reported_to_irs": True,
            }
        ]
        f = tmp_path / "robinhood_2025.json"
        f.write_text(json.dumps(consolidated_json))
        result = adapter.parse(f)

        b_forms = [f for f in result.forms if isinstance(f, Form1099B)]
        assert len(b_forms) == 1
        assert b_forms[0].proceeds == Decimal("9000.00")
        assert len(result.sales) == 1

    def test_parse_consolidated_div_only(self, adapter, tmp_path):
        data = {
            "consolidated": True,
            "payer_name": "Robinhood Securities LLC",
            "tax_year": 2025,
            "form_1099div": {
                "tax_year": 2025,
                "payer_name": "Robinhood Securities LLC",
                "ordinary_dividends": "100.00",
                "qualified_dividends": "50.00",
            },
        }
        f = tmp_path / "robinhood_2025.json"
        f.write_text(json.dumps(data))
        result = adapter.parse(f)

        assert result.form_type == FormType.ROBINHOOD_CONSOLIDATED
        assert len(result.forms) == 1
        assert isinstance(result.forms[0], Form1099DIV)
        assert result.sales == []

    def test_validate_consolidated_valid(self, adapter, consolidated_json, tmp_path):
        f = tmp_path / "robinhood_2025.json"
        f.write_text(json.dumps(consolidated_json))
        result = adapter.parse(f)
        errors = adapter.validate(result)
        assert errors == []

    def test_validate_consolidated_catches_div_errors(self, adapter, tmp_path):
        data = {
            "consolidated": True,
            "payer_name": "Robinhood Securities LLC",
            "tax_year": 2025,
            "form_1099div": {
                "tax_year": 2025,
                "payer_name": "Robinhood Securities LLC",
                "ordinary_dividends": "100.00",
                "qualified_dividends": "200.00",  # exceeds ordinary
            },
        }
        f = tmp_path / "robinhood_2025.json"
        f.write_text(json.dumps(data))
        result = adapter.parse(f)
        errors = adapter.validate(result)
        assert any("ordinary_dividends" in e for e in errors)

    def test_validate_consolidated_no_forms(self, adapter, tmp_path):
        data = {
            "consolidated": True,
            "payer_name": "Robinhood Securities LLC",
            "tax_year": 2025,
        }
        f = tmp_path / "robinhood_2025.json"
        f.write_text(json.dumps(data))
        result = adapter.parse(f)
        errors = adapter.validate(result)
        assert any("no sub-form data" in e for e in errors)
