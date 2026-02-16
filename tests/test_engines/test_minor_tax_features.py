"""Tests for minor tax features: Foreign Tax Credit CLI, QBI CLI, VPDI auto-add.

Feature 7: Foreign Tax Credit flows through and displays.
Feature 8: Section 199A QBI deduction flows through and displays.
Feature 9: VPDI from W-2 Box 14 auto-adds to SALT deduction.
"""

from decimal import Decimal

import pytest

from app.db.repository import TaxRepository
from app.db.schema import create_schema
from app.engines.estimator import TaxEstimator
from app.models.deductions import ItemizedDeductions
from app.models.enums import FilingStatus
from app.models.tax_forms import W2, Form1099DIV


@pytest.fixture
def engine():
    return TaxEstimator()


@pytest.fixture
def db_conn(tmp_path):
    db_path = tmp_path / "test.db"
    conn = create_schema(db_path)
    yield conn
    conn.close()


@pytest.fixture
def repo(db_conn):
    return TaxRepository(db_conn)


# ──────────────────────────────────────────────────────────────
# Feature 7: Foreign Tax Credit flows through estimate()
# ──────────────────────────────────────────────────────────────

class TestForeignTaxCreditFlowThrough:
    """Verify foreign tax credit is computed and included in the estimate."""

    def test_foreign_tax_credit_12_dollars(self, engine):
        """$12 foreign tax paid should yield $12 credit."""
        result = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("100000"),
            dividend_income=Decimal("500"),
            qualified_dividends=Decimal("300"),
            foreign_tax_paid=Decimal("12"),
        )
        assert result.federal_foreign_tax_credit == Decimal("12")

    def test_foreign_tax_credit_reduces_total_tax(self, engine):
        """Foreign tax credit should reduce total federal tax by $12."""
        base = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("100000"),
            dividend_income=Decimal("500"),
            qualified_dividends=Decimal("300"),
        )
        with_ftc = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("100000"),
            dividend_income=Decimal("500"),
            qualified_dividends=Decimal("300"),
            foreign_tax_paid=Decimal("12"),
        )
        assert with_ftc.federal_total_tax == base.federal_total_tax - Decimal("12")

    def test_foreign_tax_credit_from_db(self, repo, engine):
        """Foreign tax credit flows through estimate_from_db."""
        batch_id = repo.create_import_batch("manual", 2024, "test.json", "w2", 1)
        w2 = W2(
            tax_year=2024,
            employer_name="Acme Corp",
            box1_wages=Decimal("100000"),
            box2_federal_withheld=Decimal("20000"),
            box17_state_withheld=Decimal("5000"),
        )
        repo.save_w2(w2, batch_id)

        div_batch = repo.create_import_batch("manual", 2024, "div.json", "1099-div", 1)
        div = Form1099DIV(
            tax_year=2024,
            broker_name="Vanguard",
            ordinary_dividends=Decimal("500"),
            qualified_dividends=Decimal("300"),
            foreign_tax_paid=Decimal("12"),
        )
        repo.save_1099div(div, div_batch)

        result = engine.estimate_from_db(
            repo=repo,
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
        )
        assert result.federal_foreign_tax_credit == Decimal("12")


# ──────────────────────────────────────────────────────────────
# Feature 8: Section 199A QBI deduction flows through estimate()
# ──────────────────────────────────────────────────────────────

class TestQBIDeductionFlowThrough:
    """Verify Section 199A QBI deduction is computed correctly."""

    def test_199a_deduction_14_dollars(self, engine):
        """$14 in 199A dividends should yield $2.80 deduction (20%)."""
        result = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("100000"),
            dividend_income=Decimal("14"),
            section_199a_dividends=Decimal("14"),
        )
        assert result.section_199a_deduction == Decimal("2.80")

    def test_199a_deduction_reduces_taxable_income(self, engine):
        """199A deduction should lower taxable income."""
        base = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("100000"),
            dividend_income=Decimal("14"),
        )
        with_199a = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("100000"),
            dividend_income=Decimal("14"),
            section_199a_dividends=Decimal("14"),
        )
        assert with_199a.taxable_income == base.taxable_income - Decimal("2.80")

    def test_199a_from_db(self, repo, engine):
        """199A deduction flows through estimate_from_db."""
        batch_id = repo.create_import_batch("manual", 2024, "test.json", "w2", 1)
        w2 = W2(
            tax_year=2024,
            employer_name="Acme Corp",
            box1_wages=Decimal("100000"),
            box2_federal_withheld=Decimal("20000"),
            box17_state_withheld=Decimal("5000"),
        )
        repo.save_w2(w2, batch_id)

        div_batch = repo.create_import_batch("manual", 2024, "div.json", "1099-div", 1)
        div = Form1099DIV(
            tax_year=2024,
            broker_name="Vanguard",
            ordinary_dividends=Decimal("100"),
            qualified_dividends=Decimal("50"),
            section_199a_dividends=Decimal("14"),
        )
        repo.save_1099div(div, div_batch)

        result = engine.estimate_from_db(
            repo=repo,
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
        )
        assert result.section_199a_deduction == Decimal("2.80")


# ──────────────────────────────────────────────────────────────
# Feature 9: VPDI auto-add from W-2 Box 14 to SALT deduction
# ──────────────────────────────────────────────────────────────

class TestVPDIAutoAdd:
    """Verify VPDI/SDI from W-2 Box 14 is added to state income tax for SALT."""

    def test_vpdi_extracted_and_added_to_salt(self, repo, engine):
        """VPDI from Box 14 should be added to state_income_tax_paid."""
        batch_id = repo.create_import_batch("manual", 2024, "test.json", "w2", 1)
        w2 = W2(
            tax_year=2024,
            employer_name="Acme Corp",
            box1_wages=Decimal("200000"),
            box2_federal_withheld=Decimal("40000"),
            box17_state_withheld=Decimal("10000"),
            box14_other={"RSU": Decimal("50000"), "VPDI": Decimal("1760")},
        )
        repo.save_w2(w2, batch_id)

        itemized = ItemizedDeductions(
            state_income_tax_paid=Decimal("50039"),
            real_estate_taxes=Decimal("5000"),
            mortgage_interest=Decimal("12000"),
            charitable_cash=Decimal("2000"),
        )

        result = engine.estimate_from_db(
            repo=repo,
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            itemized_detail=itemized,
        )

        # VPDI should have been added: 50039 + 1760 = 51799
        assert itemized.state_income_tax_paid == Decimal("51799")
        # Warning should mention VPDI
        assert any("VPDI" in w for w in engine.warnings)

    def test_no_vpdi_means_no_change(self, repo, engine):
        """W-2 without VPDI in Box 14 should not change state_income_tax_paid."""
        batch_id = repo.create_import_batch("manual", 2024, "test.json", "w2", 1)
        w2 = W2(
            tax_year=2024,
            employer_name="Acme Corp",
            box1_wages=Decimal("200000"),
            box2_federal_withheld=Decimal("40000"),
            box17_state_withheld=Decimal("10000"),
            box14_other={"RSU": Decimal("50000")},
        )
        repo.save_w2(w2, batch_id)

        itemized = ItemizedDeductions(
            state_income_tax_paid=Decimal("50039"),
        )

        engine.estimate_from_db(
            repo=repo,
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            itemized_detail=itemized,
        )

        assert itemized.state_income_tax_paid == Decimal("50039")
        assert not any("VPDI" in w for w in engine.warnings)

    def test_multiple_w2s_vpdi_sum(self, repo, engine):
        """VPDI from multiple W-2s should sum correctly."""
        batch_id = repo.create_import_batch("manual", 2024, "test.json", "w2", 2)
        w2_1 = W2(
            tax_year=2024,
            employer_name="Acme Corp",
            box1_wages=Decimal("150000"),
            box2_federal_withheld=Decimal("30000"),
            box17_state_withheld=Decimal("8000"),
            box14_other={"VPDI": Decimal("1000")},
        )
        w2_2 = W2(
            tax_year=2024,
            employer_name="Beta Inc",
            box1_wages=Decimal("50000"),
            box2_federal_withheld=Decimal("10000"),
            box17_state_withheld=Decimal("2000"),
            box14_other={"SDI": Decimal("760")},
        )
        repo.save_w2(w2_1, batch_id)
        repo.save_w2(w2_2, batch_id)

        itemized = ItemizedDeductions(
            state_income_tax_paid=Decimal("50039"),
        )

        engine.estimate_from_db(
            repo=repo,
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            itemized_detail=itemized,
        )

        # 50039 + 1000 + 760 = 51799
        assert itemized.state_income_tax_paid == Decimal("51799")

    def test_vpdi_adds_to_uncapped_salt_but_subject_to_10k_cap(self, repo, engine):
        """VPDI increases uncapped SALT but federal cap of $10K still applies."""
        batch_id = repo.create_import_batch("manual", 2024, "test.json", "w2", 1)
        w2 = W2(
            tax_year=2024,
            employer_name="Acme Corp",
            box1_wages=Decimal("200000"),
            box2_federal_withheld=Decimal("40000"),
            box17_state_withheld=Decimal("10000"),
            box14_other={"VPDI": Decimal("1760")},
        )
        repo.save_w2(w2, batch_id)

        itemized = ItemizedDeductions(
            state_income_tax_paid=Decimal("50039"),
            real_estate_taxes=Decimal("5000"),
            mortgage_interest=Decimal("15000"),
            charitable_cash=Decimal("5000"),
        )

        result = engine.estimate_from_db(
            repo=repo,
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            itemized_detail=itemized,
        )

        detail = result.itemized_detail
        assert detail is not None
        # Uncapped SALT should include VPDI: 51799 + 5000 = 56799
        assert detail.federal_salt_uncapped == Decimal("56799")
        # Federal SALT is capped at $10K
        assert detail.federal_salt_deduction == Decimal("10000")
        # Lost to cap: 56799 - 10000 = 46799
        assert detail.federal_salt_cap_lost == Decimal("46799")

    def test_vpdi_not_added_without_itemized_detail(self, repo, engine):
        """Without itemized_detail, VPDI should not cause errors."""
        batch_id = repo.create_import_batch("manual", 2024, "test.json", "w2", 1)
        w2 = W2(
            tax_year=2024,
            employer_name="Acme Corp",
            box1_wages=Decimal("200000"),
            box2_federal_withheld=Decimal("40000"),
            box17_state_withheld=Decimal("10000"),
            box14_other={"VPDI": Decimal("1760")},
        )
        repo.save_w2(w2, batch_id)

        # No itemized_detail — should use standard deduction without error
        result = engine.estimate_from_db(
            repo=repo,
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
        )

        # Should complete without error; no VPDI warning since no itemized
        assert not any("VPDI" in w for w in engine.warnings)
        assert result.standard_deduction > Decimal("0")

    def test_vpdi_empty_box14(self, repo, engine):
        """W-2 with empty box14_other should not add any VPDI."""
        batch_id = repo.create_import_batch("manual", 2024, "test.json", "w2", 1)
        w2 = W2(
            tax_year=2024,
            employer_name="Acme Corp",
            box1_wages=Decimal("200000"),
            box2_federal_withheld=Decimal("40000"),
            box17_state_withheld=Decimal("10000"),
        )
        repo.save_w2(w2, batch_id)

        itemized = ItemizedDeductions(
            state_income_tax_paid=Decimal("50039"),
        )

        engine.estimate_from_db(
            repo=repo,
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            itemized_detail=itemized,
        )

        assert itemized.state_income_tax_paid == Decimal("50039")
