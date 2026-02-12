"""Tests for Form 1099-B PDF extractor."""

from app.parsing.extractors.form_1099b import Form1099BExtractor


class TestForm1099BExtractor:
    def setup_method(self):
        self.extractor = Form1099BExtractor()

    def test_extract_from_text_fallback(self):
        text = """Form 1099-B Proceeds From Broker 2025
Payer's name
Schwab Inc
100 shares ACME 03/15/2024 06/01/2025 $17,500.00 $15,000.00"""
        records = self.extractor.extract(text, tables=None)
        assert len(records) == 1
        assert records[0]["tax_year"] == 2025
        assert records[0]["description"] == "100 shares ACME"
        assert records[0]["proceeds"] == "17500.00"
        assert records[0]["cost_basis"] == "15000.00"

    def test_extract_from_tables(self):
        tables = [
            [
                ["Description", "Date Acquired", "Date Sold", "Proceeds", "Cost Basis"],
                ["100 sh ACME", "03/15/2024", "06/01/2025", "17,500.00", "15,000.00"],
                ["50 sh BETA", "01/01/2024", "07/15/2025", "8,000.00", "5,000.00"],
            ]
        ]
        text = "Form 1099-B 2025\nPayer's name\nSchwab"
        records = self.extractor.extract(text, tables=tables)
        assert len(records) == 2
        assert records[0]["description"] == "100 sh ACME"
        assert records[0]["proceeds"] == "17500.00"
        assert records[1]["description"] == "50 sh BETA"
        assert records[1]["proceeds"] == "8000.00"

    def test_extract_with_wash_sale(self):
        tables = [
            [
                ["Description", "Date Acquired", "Date Sold", "Proceeds", "Cost Basis", "Wash Sale Loss"],
                ["100 sh ACME", "03/15/2024", "06/01/2025", "10,000.00", "15,000.00", "5,000.00"],
            ]
        ]
        text = "Form 1099-B 2025"
        records = self.extractor.extract(text, tables=tables)
        assert len(records) == 1
        assert records[0]["wash_sale_loss_disallowed"] == "5000.00"

    def test_validate_complete(self):
        data = [
            {"description": "100 sh ACME", "date_sold": "2025-06-01", "proceeds": "17500.00"}
        ]
        errors = self.extractor.validate_extraction(data)
        assert errors == []

    def test_validate_missing_proceeds(self):
        data = [{"description": "100 sh ACME", "date_sold": "2025-06-01"}]
        errors = self.extractor.validate_extraction(data)
        assert len(errors) == 1
        assert "proceeds" in errors[0]

    def test_various_date_acquired(self):
        tables = [
            [
                ["Description", "Date Acquired", "Date Sold", "Proceeds", "Cost Basis"],
                ["100 sh ACME", "Various", "06/01/2025", "17,500.00", "0.00"],
            ]
        ]
        text = "Form 1099-B 2025"
        records = self.extractor.extract(text, tables=tables)
        assert records[0]["date_acquired"] == "Various"

    def test_empty_table_skipped(self):
        tables = [[]]
        text = "Form 1099-B 2025"
        records = self.extractor.extract(text, tables=tables)
        assert records == []
