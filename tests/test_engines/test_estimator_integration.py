"""Integration tests for TaxEstimator — estimate_from_db with real DB."""

from datetime import date
from decimal import Decimal

import pytest

from app.db.repository import TaxRepository
from app.db.schema import create_schema
from app.engines.estimator import TaxEstimator
from app.models.enums import (
    AdjustmentCode,
    BrokerSource,
    EquityType,
    FilingStatus,
    Form8949Category,
    HoldingPeriod,
    TransactionType,
)
from app.models.equity_event import EquityEvent, Lot, Sale, SaleResult, Security
from app.models.tax_forms import W2, Form1099DIV, Form1099INT


@pytest.fixture
def db_conn(tmp_path):
    db_path = tmp_path / "test.db"
    conn = create_schema(db_path)
    yield conn
    conn.close()


@pytest.fixture
def repo(db_conn):
    return TaxRepository(db_conn)


@pytest.fixture
def engine():
    return TaxEstimator()


class TestEstimateFromDBW2Only:
    """Import W-2 and estimate — no capital gains."""

    def test_w2_only(self, repo, engine):
        w2 = W2(
            tax_year=2024,
            employer_name="Acme Corp",
            box1_wages=Decimal("150000"),
            box2_federal_withheld=Decimal("25000"),
            box17_state_withheld=Decimal("8000"),
        )
        batch_id = repo.create_import_batch("manual", 2024, "test.json", "w2", 1)
        repo.save_w2(w2, batch_id)

        result = engine.estimate_from_db(
            repo=repo,
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
        )

        assert result.w2_wages == Decimal("150000")
        assert result.federal_withheld == Decimal("25000")
        assert result.ca_withheld == Decimal("8000")
        assert result.federal_regular_tax == Decimal("25538.50")
        assert result.total_balance_due == Decimal("2626.13")

    def test_multiple_w2s(self, repo, engine):
        batch_id = repo.create_import_batch("manual", 2024, "test.json", "w2", 2)
        w2_1 = W2(
            tax_year=2024,
            employer_name="Acme Corp",
            box1_wages=Decimal("100000"),
            box2_federal_withheld=Decimal("18000"),
            box17_state_withheld=Decimal("6000"),
        )
        w2_2 = W2(
            tax_year=2024,
            employer_name="Beta Inc",
            box1_wages=Decimal("50000"),
            box2_federal_withheld=Decimal("7000"),
            box17_state_withheld=Decimal("2000"),
        )
        repo.save_w2(w2_1, batch_id)
        repo.save_w2(w2_2, batch_id)

        result = engine.estimate_from_db(
            repo=repo,
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
        )

        assert result.w2_wages == Decimal("150000")
        assert result.federal_withheld == Decimal("25000")
        assert result.ca_withheld == Decimal("8000")


class TestEstimateFromDBWithDividendsAndInterest:
    """W-2 + 1099-DIV + 1099-INT."""

    def test_all_income_types(self, repo, engine):
        batch_id = repo.create_import_batch("manual", 2024, "test.json", "w2", 1)

        w2 = W2(
            tax_year=2024,
            employer_name="Acme Corp",
            box1_wages=Decimal("250000"),
            box2_federal_withheld=Decimal("45000"),
            box17_state_withheld=Decimal("18000"),
        )
        repo.save_w2(w2, batch_id)

        div = Form1099DIV(
            tax_year=2024,
            broker_name="Fidelity",
            ordinary_dividends=Decimal("12000"),
            qualified_dividends=Decimal("10000"),
            total_capital_gain_distributions=Decimal("0"),
            federal_tax_withheld=Decimal("0"),
        )
        repo.save_1099div(div, batch_id)

        intform = Form1099INT(
            tax_year=2024,
            payer_name="Bank of America",
            interest_income=Decimal("5000"),
            early_withdrawal_penalty=Decimal("0"),
            federal_tax_withheld=Decimal("0"),
        )
        repo.save_1099int(intform, batch_id)

        result = engine.estimate_from_db(
            repo=repo,
            tax_year=2024,
            filing_status=FilingStatus.MFJ,
        )

        assert result.total_income == Decimal("267000")
        assert result.dividend_income == Decimal("12000")
        assert result.qualified_dividends == Decimal("10000")
        assert result.interest_income == Decimal("5000")
        assert result.federal_regular_tax == Decimal("40757")
        assert result.federal_ltcg_tax == Decimal("1500")


class TestEstimateFromDBNoData:
    """Empty database produces warnings."""

    def test_no_data_warnings(self, repo, engine):
        result = engine.estimate_from_db(
            repo=repo,
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
        )

        assert result.w2_wages == Decimal("0")
        assert result.total_income == Decimal("0")
        assert any("No W-2 data" in w for w in engine.warnings)
        assert any("No reconciliation" in w for w in engine.warnings)


def _create_lot_and_sale(repo, security, lot_id, sale_id, acq_date, sale_date, shares,
                         cost_per_share, proceeds_per_share):
    """Helper: create Event + Lot + Sale to satisfy FK constraints before saving SaleResult."""
    event = EquityEvent(
        id=f"evt-{lot_id}",
        event_type=TransactionType.VEST,
        equity_type=EquityType.RSU,
        security=security,
        event_date=acq_date,
        shares=shares,
        price_per_share=cost_per_share,
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_event(event)
    lot = Lot(
        id=lot_id,
        equity_type=EquityType.RSU,
        security=security,
        acquisition_date=acq_date,
        shares=shares,
        cost_per_share=cost_per_share,
        shares_remaining=Decimal("0"),
        source_event_id=f"evt-{lot_id}",
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_lot(lot)
    sale = Sale(
        id=sale_id,
        lot_id=lot_id,
        security=security,
        sale_date=sale_date,
        shares=shares,
        proceeds_per_share=proceeds_per_share,
        broker_reported_basis=Decimal("0"),
        basis_reported_to_irs=True,
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_sale(sale)


class TestEstimateFromDBWithReconciliation:
    """Full pipeline: W-2 + reconciled sale results."""

    def test_with_sale_results(self, repo, engine):
        batch_id = repo.create_import_batch("manual", 2024, "test.json", "w2", 1)

        w2 = W2(
            tax_year=2024,
            employer_name="Acme Corp",
            box1_wages=Decimal("200000"),
            box2_federal_withheld=Decimal("40000"),
            box17_state_withheld=Decimal("15000"),
        )
        repo.save_w2(w2, batch_id)

        security = Security(ticker="ACME", name="Acme Corp")
        _create_lot_and_sale(
            repo, security, "lot-001", "sale-001",
            date(2023, 1, 1), date(2024, 6, 1),
            Decimal("100"), Decimal("150"), Decimal("175"),
        )
        sr = SaleResult(
            sale_id="sale-001",
            lot_id="lot-001",
            security=security,
            acquisition_date=date(2023, 1, 1),
            sale_date=date(2024, 6, 1),
            shares=Decimal("100"),
            proceeds=Decimal("17500"),
            broker_reported_basis=Decimal("0"),
            correct_basis=Decimal("15000"),
            adjustment_amount=Decimal("15000"),
            adjustment_code=AdjustmentCode.B,
            holding_period=HoldingPeriod.LONG_TERM,
            form_8949_category=Form8949Category.B,
            gain_loss=Decimal("2500"),
            ordinary_income=Decimal("0"),
            amt_adjustment=Decimal("0"),
            wash_sale_disallowed=Decimal("0"),
            notes="RSU sale",
        )
        repo.save_sale_result(sr)

        result = engine.estimate_from_db(
            repo=repo,
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
        )

        assert result.long_term_gains == Decimal("2500")
        assert result.total_income == Decimal("202500")


class TestEstimateFromDBCapitalLossNetting:
    """Capital loss netting via estimate_from_db."""

    def test_loss_limited(self, repo, engine):
        batch_id = repo.create_import_batch("manual", 2025, "test.json", "w2", 1)

        w2 = W2(
            tax_year=2025,
            employer_name="Acme Corp",
            box1_wages=Decimal("100000"),
            box2_federal_withheld=Decimal("15000"),
            box17_state_withheld=Decimal("5000"),
        )
        repo.save_w2(w2, batch_id)

        security = Security(ticker="ACME", name="Acme Corp")
        _create_lot_and_sale(
            repo, security, "lot-loss", "sale-loss",
            date(2025, 1, 1), date(2025, 6, 1),
            Decimal("100"), Decimal("100"), Decimal("20"),
        )
        _create_lot_and_sale(
            repo, security, "lot-gain", "sale-gain",
            date(2024, 1, 1), date(2025, 6, 15),
            Decimal("20"), Decimal("100"), Decimal("200"),
        )
        sr1 = SaleResult(
            sale_id="sale-loss",
            lot_id="lot-loss",
            security=security,
            acquisition_date=date(2025, 1, 1),
            sale_date=date(2025, 6, 1),
            shares=Decimal("100"),
            proceeds=Decimal("2000"),
            broker_reported_basis=Decimal("10000"),
            correct_basis=Decimal("10000"),
            adjustment_amount=Decimal("0"),
            adjustment_code=AdjustmentCode.NONE,
            holding_period=HoldingPeriod.SHORT_TERM,
            form_8949_category=Form8949Category.A,
            gain_loss=Decimal("-8000"),
            ordinary_income=Decimal("0"),
            amt_adjustment=Decimal("0"),
            wash_sale_disallowed=Decimal("0"),
            notes="ST loss",
        )
        sr2 = SaleResult(
            sale_id="sale-gain",
            lot_id="lot-gain",
            security=security,
            acquisition_date=date(2024, 1, 1),
            sale_date=date(2025, 6, 15),
            shares=Decimal("20"),
            proceeds=Decimal("4000"),
            broker_reported_basis=Decimal("2000"),
            correct_basis=Decimal("2000"),
            adjustment_amount=Decimal("0"),
            adjustment_code=AdjustmentCode.NONE,
            holding_period=HoldingPeriod.LONG_TERM,
            form_8949_category=Form8949Category.D,
            gain_loss=Decimal("2000"),
            ordinary_income=Decimal("0"),
            amt_adjustment=Decimal("0"),
            wash_sale_disallowed=Decimal("0"),
            notes="LT gain",
        )
        repo.save_sale_result(sr1)
        repo.save_sale_result(sr2)

        result = engine.estimate_from_db(
            repo=repo,
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
        )

        # Net capital = -8000 + 2000 = -6000, limited to -3000
        assert result.total_income == Decimal("97000")
        assert any("carries forward" in w for w in engine.warnings)


class TestEstimateFromDBOrdinaryIncomeWarning:
    """Warns about equity compensation ordinary income."""

    def test_ordinary_income_warning(self, repo, engine):
        batch_id = repo.create_import_batch("manual", 2024, "test.json", "w2", 1)

        w2 = W2(
            tax_year=2024,
            employer_name="Acme Corp",
            box1_wages=Decimal("180000"),
            box2_federal_withheld=Decimal("30000"),
        )
        repo.save_w2(w2, batch_id)

        security = Security(ticker="ACME", name="Acme Corp")
        _create_lot_and_sale(
            repo, security, "lot-espp", "sale-espp",
            date(2024, 1, 1), date(2024, 6, 1),
            Decimal("50"), Decimal("127.50"), Decimal("160"),
        )
        sr = SaleResult(
            sale_id="sale-espp",
            lot_id="lot-espp",
            security=security,
            acquisition_date=date(2024, 1, 1),
            sale_date=date(2024, 6, 1),
            shares=Decimal("50"),
            proceeds=Decimal("8000"),
            broker_reported_basis=Decimal("6375"),
            correct_basis=Decimal("7500"),
            adjustment_amount=Decimal("1125"),
            adjustment_code=AdjustmentCode.B,
            holding_period=HoldingPeriod.SHORT_TERM,
            form_8949_category=Form8949Category.B,
            gain_loss=Decimal("500"),
            ordinary_income=Decimal("1125"),
            amt_adjustment=Decimal("0"),
            wash_sale_disallowed=Decimal("0"),
            notes="ESPP disqualifying",
        )
        repo.save_sale_result(sr)

        engine.estimate_from_db(
            repo=repo,
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
        )

        assert any("double-counting" in w for w in engine.warnings)
