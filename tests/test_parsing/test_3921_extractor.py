"""Tests for Form 3921 PDF extractor."""

from pathlib import Path

import pdfplumber

from app.parsing.extractors.form_3921 import Form3921Extractor


class TestForm3921Extractor:
    def setup_method(self):
        self.extractor = Form3921Extractor()

    def test_extract_from_text(self):
        text = """Form 3921 Exercise of an Incentive Stock Option 2025
Transferor's name
Acme Corp
1 Date of grant 01/15/2022
2 Date of exercise 03/01/2025
3 Exercise price per share $50.00
4 Fair market value per share on exercise date $120.00
5 No. of shares transferred 200"""
        records = self.extractor.extract(text)
        assert len(records) == 1
        record = records[0]
        assert record["tax_year"] == 2025
        assert record["employer_name"] == "Acme Corp"
        assert record["grant_date"] == "2022-01-15"
        assert record["exercise_date"] == "2025-03-01"
        assert record["exercise_price_per_share"] == "50.00"
        assert record["fmv_on_exercise_date"] == "120.00"
        assert record["shares_transferred"] == "200"

    def test_validate_complete(self):
        data = [
            {
                "tax_year": 2025,
                "exercise_date": "2025-03-01",
                "exercise_price_per_share": "50.00",
                "fmv_on_exercise_date": "120.00",
                "shares_transferred": "200",
            }
        ]
        errors = self.extractor.validate_extraction(data)
        assert errors == []

    def test_validate_missing_fields(self):
        data = [{"tax_year": 2025}]
        errors = self.extractor.validate_extraction(data)
        assert len(errors) == 4  # exercise_date, exercise_price, fmv, shares

    def test_extract_from_pdf(self, form3921_pdf: Path):
        """Integration test with synthetic PDF."""
        with pdfplumber.open(form3921_pdf) as pdf:
            text = ""
            for page in pdf.pages:
                text += (page.extract_text() or "") + "\n"
        records = self.extractor.extract(text)
        assert len(records) == 1
        assert records[0]["exercise_price_per_share"] == "50.00"
