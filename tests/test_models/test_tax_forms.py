"""Tests for tax form models."""

from decimal import Decimal

from app.models.tax_forms import W2, Form3921, Form3922


class TestW2:
    def test_create_w2(self, sample_w2: W2):
        assert sample_w2.box1_wages == Decimal("250000")
        assert sample_w2.state == "CA"

    def test_box12_codes(self, sample_w2: W2):
        assert sample_w2.box12_codes["V"] == Decimal("5000")


class TestForm3921:
    def test_spread_per_share(self, sample_form3921: Form3921):
        assert sample_form3921.spread_per_share == Decimal("70.00")

    def test_total_amt_preference(self, sample_form3921: Form3921):
        assert sample_form3921.total_amt_preference == Decimal("14000.00")


class TestForm3922:
    def test_discount_per_share(self, sample_form3922: Form3922):
        assert sample_form3922.discount_per_share == Decimal("22.50")
