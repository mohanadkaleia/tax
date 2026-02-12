"""Tests for Form 3922 PDF extractor."""

from pathlib import Path

import pdfplumber

from app.parsing.extractors.form_3922 import Form3922Extractor


class TestForm3922Extractor:
    def setup_method(self):
        self.extractor = Form3922Extractor()

    def test_extract_from_text(self):
        text = """Form 3922 Transfer of Stock Acquired Through Employee Stock Purchase Plan 2025
Transferor's name
Acme Corp
1 Date of option grant 01/01/2024
2 Date of transfer 06/30/2024
3 FMV on grant date $140.00
4 FMV on transfer date $150.00
5 Price paid per share $127.50
6 No. of shares transferred 50"""
        records = self.extractor.extract(text)
        assert len(records) == 1
        record = records[0]
        assert record["tax_year"] == 2025
        assert record["employer_name"] == "Acme Corp"
        assert record["offering_date"] == "2024-01-01"
        assert record["purchase_date"] == "2024-06-30"
        assert record["fmv_on_offering_date"] == "140.00"
        assert record["fmv_on_purchase_date"] == "150.00"
        assert record["purchase_price_per_share"] == "127.50"
        assert record["shares_transferred"] == "50"

    def test_validate_complete(self):
        data = [
            {
                "tax_year": 2025,
                "purchase_date": "2024-06-30",
                "fmv_on_purchase_date": "150.00",
                "purchase_price_per_share": "127.50",
                "shares_transferred": "50",
            }
        ]
        errors = self.extractor.validate_extraction(data)
        assert errors == []

    def test_validate_missing_fields(self):
        data = [{"tax_year": 2025}]
        errors = self.extractor.validate_extraction(data)
        assert len(errors) == 4  # purchase_date, fmv, price, shares

    def test_extract_from_pdf(self, form3922_pdf: Path):
        """Integration test with synthetic PDF."""
        with pdfplumber.open(form3922_pdf) as pdf:
            text = ""
            for page in pdf.pages:
                text += (page.extract_text() or "") + "\n"
        records = self.extractor.extract(text)
        assert len(records) == 1
        assert records[0]["purchase_price_per_share"] == "127.50"
