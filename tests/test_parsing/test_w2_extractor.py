"""Tests for W-2 PDF extractor."""

from pathlib import Path

import pdfplumber

from app.parsing.extractors.w2 import W2Extractor


class TestW2Extractor:
    def setup_method(self):
        self.extractor = W2Extractor()

    def test_extract_from_text(self):
        text = """Form W-2 Wage and Tax Statement 2025
Employer's name
Acme Corp
1 Wages, tips, other comp 250,000.00
2 Federal income tax withheld 55,000.00
16 State wages 250,000.00
17 State income tax 22,000.00
12a V 5,000.00
14 Other
RSU 50,000.00
ESPP 3,000.00"""
        data = self.extractor.extract(text)
        assert data["tax_year"] == 2025
        assert data["employer_name"] == "Acme Corp"
        assert data["box1_wages"] == "250000.00"
        assert data["box2_federal_withheld"] == "55000.00"
        assert data["state"] == "CA"

    def test_extract_box12_codes(self):
        text = """Form W-2 2025
Employer's name
Test Corp
1 Wages, tips, other comp 100,000.00
2 Federal income tax withheld 20,000.00
12a V 5,000.00"""
        data = self.extractor.extract(text)
        assert "box12_codes" in data
        assert data["box12_codes"]["V"] == "5000.00"

    def test_extract_box14_other(self):
        text = """Form W-2 2025
Employer's name
Test Corp
1 Wages, tips, other comp 100,000.00
2 Federal income tax withheld 20,000.00
14 Other
RSU 50,000.00"""
        data = self.extractor.extract(text)
        assert "box14_other" in data
        assert data["box14_other"]["RSU"] == "50000.00"

    def test_validate_complete(self):
        data = {
            "tax_year": 2025,
            "employer_name": "Acme",
            "box1_wages": "250000.00",
            "box2_federal_withheld": "55000.00",
        }
        errors = self.extractor.validate_extraction(data)
        assert errors == []

    def test_validate_missing_fields(self):
        data = {"tax_year": 2025}
        errors = self.extractor.validate_extraction(data)
        assert len(errors) == 3  # employer_name, box1, box2

    def test_extract_boxes_3_through_6(self):
        text = """Form W-2 2025
Employer's name
Acme Corp
1 Wages, tips, other comp 250,000.00
2 Federal income tax withheld 55,000.00
3 Social security wages 168,600.00
4 Social security tax withheld 10,453.20
5 Medicare wages and tips 250,000.00
6 Medicare tax withheld 3,625.00"""
        data = self.extractor.extract(text)
        assert data["box3_ss_wages"] == "168600.00"
        assert data["box4_ss_withheld"] == "10453.20"
        assert data["box5_medicare_wages"] == "250000.00"
        assert data["box6_medicare_withheld"] == "3625.00"

    def test_extract_employer_ein(self):
        text = """Form W-2 2025
b Employer's identification number
12-3456789
Employer's name
Acme Corp
1 Wages, tips, other comp 100,000.00
2 Federal income tax withheld 20,000.00"""
        data = self.extractor.extract(text)
        assert data["employer_ein"] == "12-3456789"

    def test_validate_box2_equals_box1_not_hard_error(self):
        """Box 2 == Box 1 should NOT be a hard validation error."""
        data = {
            "tax_year": 2025,
            "employer_name": "Acme",
            "box1_wages": "250000.00",
            "box2_federal_withheld": "250000.00",
        }
        errors = self.extractor.validate_extraction(data)
        assert errors == []  # No hard errors — all required fields present

    def test_warn_box2_equals_box1_ocr_error(self):
        """Box 2 == Box 1 is a classic OCR duplication warning."""
        data = {
            "tax_year": 2025,
            "employer_name": "Acme",
            "box1_wages": "250000.00",
            "box2_federal_withheld": "250000.00",
        }
        warnings = self.extractor.get_warnings(data)
        assert any("Box 2" in w and "equals Box 1" in w for w in warnings)

    def test_warn_box2_exceeds_box1(self):
        data = {
            "tax_year": 2025,
            "employer_name": "Acme",
            "box1_wages": "100000.00",
            "box2_federal_withheld": "150000.00",
        }
        warnings = self.extractor.get_warnings(data)
        assert any("exceeds Box 1" in w for w in warnings)

    def test_warn_high_withholding_ratio(self):
        data = {
            "tax_year": 2025,
            "employer_name": "Acme",
            "box1_wages": "100000.00",
            "box2_federal_withheld": "55000.00",
        }
        warnings = self.extractor.get_warnings(data)
        assert any("unusually high" in w for w in warnings)

    def test_warn_invalid_box12_code(self):
        """OCR misread like 'CC' should be flagged as warning."""
        data = {
            "tax_year": 2025,
            "employer_name": "Acme",
            "box1_wages": "100000.00",
            "box2_federal_withheld": "20000.00",
            "box12_codes": {"CC": "5000.00"},
        }
        warnings = self.extractor.get_warnings(data)
        assert any("CC" in w and "not a recognized" in w for w in warnings)

    def test_warn_valid_box12_codes_no_warnings(self):
        data = {
            "tax_year": 2025,
            "employer_name": "Acme",
            "box1_wages": "100000.00",
            "box2_federal_withheld": "20000.00",
            "box12_codes": {"DD": "5000.00", "V": "10000.00", "W": "3000.00"},
        }
        warnings = self.extractor.get_warnings(data)
        assert warnings == []

    def test_warn_box4_exceeds_ss_cap(self):
        data = {
            "tax_year": 2025,
            "employer_name": "Acme",
            "box1_wages": "250000.00",
            "box2_federal_withheld": "55000.00",
            "box4_ss_withheld": "50000.00",
        }
        warnings = self.extractor.get_warnings(data)
        assert any("Box 4" in w and "exceeds maximum SS tax" in w for w in warnings)

    def test_warn_box3_exceeds_ss_wage_cap(self):
        data = {
            "tax_year": 2025,
            "employer_name": "Acme",
            "box1_wages": "250000.00",
            "box2_federal_withheld": "55000.00",
            "box3_ss_wages": "200000.00",
        }
        warnings = self.extractor.get_warnings(data)
        assert any("Box 3" in w and "SS wage cap" in w for w in warnings)

    def test_plausible_data_no_warnings(self):
        """Fully plausible W-2 data should produce no warnings."""
        data = {
            "tax_year": 2025,
            "employer_name": "Acme",
            "box1_wages": "250000.00",
            "box2_federal_withheld": "55000.00",
            "box3_ss_wages": "168600.00",
            "box4_ss_withheld": "10453.20",
            "box5_medicare_wages": "250000.00",
            "box6_medicare_withheld": "3625.00",
            "box12_codes": {"DD": "8000.00"},
        }
        warnings = self.extractor.get_warnings(data)
        assert warnings == []

    def test_extract_from_pdf(self, w2_pdf: Path):
        """Integration test: extract from a real PDF file."""
        with pdfplumber.open(w2_pdf) as pdf:
            text = ""
            for page in pdf.pages:
                text += (page.extract_text() or "") + "\n"
        data = self.extractor.extract(text)
        assert data["tax_year"] == 2025
        assert data["box1_wages"] == "250000.00"
