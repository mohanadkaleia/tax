"""Tests for form type auto-detection."""

from app.parsing.detector import FormType, detect_form_type


class TestFormDetection:
    def test_detect_w2(self):
        text = "Form W-2 Wage and Tax Statement 2025"
        assert detect_form_type(text) == FormType.W2

    def test_detect_1099b(self):
        text = "Form 1099-B Proceeds From Broker and Barter Exchange Transactions"
        assert detect_form_type(text) == FormType.FORM_1099B

    def test_detect_1099div(self):
        text = "Form 1099-DIV Dividends and Distributions 2025"
        assert detect_form_type(text) == FormType.FORM_1099DIV

    def test_detect_1099int(self):
        text = "Form 1099-INT Interest Income 2025"
        assert detect_form_type(text) == FormType.FORM_1099INT

    def test_detect_3921(self):
        text = "Form 3921 Exercise of an Incentive Stock Option Under Section 422(b)"
        assert detect_form_type(text) == FormType.FORM_3921

    def test_detect_3922(self):
        text = "Form 3922 Transfer of Stock Acquired Through an Employee Stock Purchase Plan"
        assert detect_form_type(text) == FormType.FORM_3922

    def test_detect_unknown(self):
        text = "This is just some random text with no form identifiers."
        assert detect_form_type(text) is None

    def test_detect_case_insensitive(self):
        text = "FORM W-2 WAGE AND TAX STATEMENT"
        assert detect_form_type(text) == FormType.W2

    def test_detect_partial_match(self):
        text = "This document contains Wage and Tax Statement information"
        assert detect_form_type(text) == FormType.W2

    def test_3921_takes_priority_over_w2(self):
        """Form 3921 should be detected before W-2 since it's more specific."""
        text = "Form 3921 Form W-2"
        assert detect_form_type(text) == FormType.FORM_3921
