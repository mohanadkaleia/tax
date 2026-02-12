"""Tests for PII redaction."""

from app.parsing.redactor import Redactor


class TestRedactor:
    def setup_method(self):
        self.redactor = Redactor()

    def test_redact_ssn(self):
        text = "Employee SSN: 123-45-6789"
        result = self.redactor.redact(text)
        assert "123-45-6789" not in result.text
        assert "***-**-****" in result.text
        assert any("SSN" in r for r in result.redactions_made)

    def test_redact_ein(self):
        text = "Employer EIN: 12-3456789"
        result = self.redactor.redact(text)
        assert "12-3456789" not in result.text
        assert "**-*******" in result.text
        assert any("EIN" in r for r in result.redactions_made)

    def test_redact_account_number(self):
        text = "Account Number: ABC-12345"
        result = self.redactor.redact(text)
        assert "ABC-12345" not in result.text
        assert "[REDACTED]" in result.text

    def test_redact_recipient_tin(self):
        text = "Recipient's TIN 123-45-6789"
        result = self.redactor.redact(text)
        assert "123-45-6789" not in result.text

    def test_redact_payer_tin(self):
        text = "Payer's TIN 98-7654321"
        result = self.redactor.redact(text)
        assert "98-7654321" not in result.text

    def test_no_pii_returns_unchanged(self):
        text = "Box 1 Wages 250,000.00"
        result = self.redactor.redact(text)
        assert result.text == text
        assert result.redactions_made == []

    def test_multiple_ssns(self):
        text = "SSN1: 111-22-3333 SSN2: 444-55-6666"
        result = self.redactor.redact(text)
        assert "111-22-3333" not in result.text
        assert "444-55-6666" not in result.text
        assert any("2 occurrence" in r for r in result.redactions_made)

    def test_scrub_output_removes_ein(self):
        data = {"employer_name": "Acme", "employer_ein": "12-3456789", "box1_wages": "250000.00"}
        scrubbed = self.redactor.scrub_output(data)
        assert scrubbed["employer_ein"] is None
        assert scrubbed["employer_name"] == "Acme"
        assert scrubbed["box1_wages"] == "250000.00"

    def test_scrub_output_no_ein_field(self):
        data = {"interest_income": "456.78"}
        scrubbed = self.redactor.scrub_output(data)
        assert scrubbed == data
