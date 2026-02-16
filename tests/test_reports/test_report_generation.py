"""Tests for report generation CLI and generators.

Tests cover:
- Form 8949 report generation from sale results
- Reconciliation report generation
- Tax summary report generation
- CLI report command (mock DB, verify files created)
- Graceful handling of missing data (no sales -> skip report)
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import app
from app.db.repository import TaxRepository
from app.db.schema import create_schema
from app.models.enums import AdjustmentCode, FilingStatus, Form8949Category, HoldingPeriod
from app.models.equity_event import SaleResult, Security
from app.models.reports import ReconciliationLine, TaxEstimate
from app.reports import Form8949Generator, ReconciliationReportGenerator, TaxSummaryGenerator

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_security() -> Security:
    return Security(ticker="ACME", name="Acme Corp")


@pytest.fixture
def sample_sale_result(sample_security: Security) -> SaleResult:
    return SaleResult(
        sale_id="sale-1",
        lot_id="lot-1",
        security=sample_security,
        acquisition_date=date(2024, 3, 15),
        sale_date=date(2025, 6, 1),
        shares=Decimal("100"),
        proceeds=Decimal("17500.00"),
        broker_reported_basis=Decimal("0"),
        correct_basis=Decimal("15000.00"),
        adjustment_amount=Decimal("15000.00"),
        adjustment_code=AdjustmentCode.B,
        holding_period=HoldingPeriod.LONG_TERM,
        form_8949_category=Form8949Category.D,
        gain_loss=Decimal("2500.00"),
    )


@pytest.fixture
def sample_reconciliation_line() -> ReconciliationLine:
    return ReconciliationLine(
        sale_id="sale-1",
        security="ACME",
        sale_date=date(2025, 6, 1),
        shares=Decimal("100"),
        broker_proceeds=Decimal("17500.00"),
        broker_basis=Decimal("0"),
        correct_basis=Decimal("15000.00"),
        adjustment=Decimal("15000.00"),
        adjustment_code=AdjustmentCode.B,
        gain_loss_broker=Decimal("17500.00"),
        gain_loss_correct=Decimal("2500.00"),
        difference=Decimal("-15000.00"),
    )


@pytest.fixture
def sample_tax_estimate() -> TaxEstimate:
    return TaxEstimate(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        w2_wages=Decimal("150000"),
        interest_income=Decimal("500"),
        dividend_income=Decimal("1200"),
        qualified_dividends=Decimal("800"),
        short_term_gains=Decimal("3000"),
        long_term_gains=Decimal("5000"),
        total_income=Decimal("159700"),
        agi=Decimal("159700"),
        standard_deduction=Decimal("15700"),
        deduction_used=Decimal("15700"),
        taxable_income=Decimal("144000"),
        federal_regular_tax=Decimal("25000"),
        federal_ltcg_tax=Decimal("750"),
        federal_niit=Decimal("0"),
        federal_amt=Decimal("0"),
        federal_total_tax=Decimal("25750"),
        federal_withheld=Decimal("22000"),
        federal_balance_due=Decimal("3750"),
        ca_taxable_income=Decimal("144000"),
        ca_tax=Decimal("10500"),
        ca_mental_health_tax=Decimal("0"),
        ca_total_tax=Decimal("10500"),
        ca_withheld=Decimal("9000"),
        ca_balance_due=Decimal("1500"),
        total_tax=Decimal("36250"),
        total_withheld=Decimal("31000"),
        total_balance_due=Decimal("5250"),
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Create a temporary database with schema."""
    path = tmp_path / "test_taxbot.db"
    conn = create_schema(path)
    conn.close()
    return path


@pytest.fixture
def populated_db(db_path: Path) -> Path:
    """Create a database populated with test data for report generation."""
    conn = create_schema(db_path)
    repo = TaxRepository(conn)

    # Create import batch
    batch_id = repo.create_import_batch(
        source="manual",
        tax_year=2025,
        file_path="test.json",
        form_type="w2",
        record_count=1,
    )

    # Insert a W-2
    from app.models.tax_forms import W2
    w2 = W2(
        tax_year=2025,
        employer_name="Test Corp",
        box1_wages=Decimal("150000"),
        box2_federal_withheld=Decimal("22000"),
        box5_medicare_wages=Decimal("150000"),
        box6_medicare_withheld=Decimal("2175"),
        box16_state_wages=Decimal("150000"),
        box17_state_withheld=Decimal("9000"),
    )
    repo.save_w2(w2, batch_id)

    # Insert an equity event (RSU vest)
    from app.models.enums import BrokerSource, EquityType, TransactionType
    from app.models.equity_event import EquityEvent, Lot, Sale
    event = EquityEvent(
        id="evt-1",
        event_type=TransactionType.VEST,
        equity_type=EquityType.RSU,
        security=Security(ticker="ACME", name="Acme Corp"),
        event_date=date(2024, 3, 15),
        shares=Decimal("100"),
        price_per_share=Decimal("150.00"),
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_event(event, batch_id)

    # Insert a lot
    lot = Lot(
        id="lot-1",
        equity_type=EquityType.RSU,
        security=Security(ticker="ACME", name="Acme Corp"),
        acquisition_date=date(2024, 3, 15),
        shares=Decimal("100"),
        cost_per_share=Decimal("150.00"),
        shares_remaining=Decimal("0"),
        source_event_id="evt-1",
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_lot(lot, batch_id)

    # Insert a sale
    sale = Sale(
        id="sale-1",
        lot_id="lot-1",
        security=Security(ticker="ACME", name="Acme Corp"),
        date_acquired=date(2024, 3, 15),
        sale_date=date(2025, 6, 1),
        shares=Decimal("100"),
        proceeds_per_share=Decimal("175.00"),
        broker_reported_basis=Decimal("0"),
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_sale(sale, batch_id)

    # Insert a sale result
    result = SaleResult(
        sale_id="sale-1",
        lot_id="lot-1",
        security=Security(ticker="ACME", name="Acme Corp"),
        acquisition_date=date(2024, 3, 15),
        sale_date=date(2025, 6, 1),
        shares=Decimal("100"),
        proceeds=Decimal("17500.00"),
        broker_reported_basis=Decimal("0"),
        correct_basis=Decimal("15000.00"),
        adjustment_amount=Decimal("15000.00"),
        adjustment_code=AdjustmentCode.B,
        holding_period=HoldingPeriod.LONG_TERM,
        form_8949_category=Form8949Category.D,
        gain_loss=Decimal("2500.00"),
    )
    repo.save_sale_result(result)

    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Form 8949 Report Tests
# ---------------------------------------------------------------------------

class TestForm8949ReportGeneration:
    """Tests for Form 8949 report generation from sale results."""

    def test_generate_and_render(self, sample_sale_result: SaleResult) -> None:
        gen = Form8949Generator()
        lines = gen.generate_lines([sample_sale_result])
        assert len(lines) == 1
        assert lines[0].proceeds == Decimal("17500.00")
        assert lines[0].cost_basis == Decimal("15000.00")
        assert lines[0].adjustment_code == AdjustmentCode.B

        content = gen.render(lines)
        assert "FORM 8949" in content
        assert "$17500.00" in content
        assert "$15000.00" in content

    def test_generate_multiple_lines(self, sample_security: Security) -> None:
        results = [
            SaleResult(
                sale_id=f"sale-{i}",
                lot_id=f"lot-{i}",
                security=sample_security,
                acquisition_date=date(2024, 1, i + 1),
                sale_date=date(2025, 6, 1),
                shares=Decimal("50"),
                proceeds=Decimal("8750.00"),
                broker_reported_basis=Decimal("0"),
                correct_basis=Decimal("7500.00"),
                adjustment_amount=Decimal("7500.00"),
                adjustment_code=AdjustmentCode.B,
                holding_period=HoldingPeriod.LONG_TERM,
                form_8949_category=Form8949Category.D,
                gain_loss=Decimal("1250.00"),
            )
            for i in range(3)
        ]
        gen = Form8949Generator()
        lines = gen.generate_lines(results)
        assert len(lines) == 3

        content = gen.render(lines)
        assert content.count("$8750.00") == 3

    def test_empty_results(self) -> None:
        gen = Form8949Generator()
        lines = gen.generate_lines([])
        assert lines == []
        content = gen.render(lines)
        assert "FORM 8949" in content


# ---------------------------------------------------------------------------
# Reconciliation Report Tests
# ---------------------------------------------------------------------------

class TestReconciliationReportGeneration:
    """Tests for reconciliation report generation."""

    def test_render(self, sample_reconciliation_line: ReconciliationLine) -> None:
        gen = ReconciliationReportGenerator()
        content = gen.render([sample_reconciliation_line])
        assert "Reconciliation Report" in content
        assert "sale-1" in content
        assert "ACME" in content
        assert "$17500.00" in content
        assert "$15000.00" in content

    def test_render_with_none_broker_basis(self) -> None:
        line = ReconciliationLine(
            sale_id="sale-2",
            security="XYZ",
            sale_date=date(2025, 7, 1),
            shares=Decimal("50"),
            broker_proceeds=Decimal("10000.00"),
            broker_basis=None,
            correct_basis=Decimal("8000.00"),
            adjustment=Decimal("8000.00"),
            adjustment_code=AdjustmentCode.B,
            gain_loss_broker=None,
            gain_loss_correct=Decimal("2000.00"),
            difference=Decimal("2000.00"),
        )
        gen = ReconciliationReportGenerator()
        content = gen.render([line])
        assert "N/A" in content
        assert "XYZ" in content


# ---------------------------------------------------------------------------
# Tax Summary Report Tests
# ---------------------------------------------------------------------------

class TestTaxSummaryReportGeneration:
    """Tests for tax summary report generation."""

    def test_render(self, sample_tax_estimate: TaxEstimate) -> None:
        gen = TaxSummaryGenerator()
        content = gen.render(sample_tax_estimate)
        assert "Tax Estimate Summary" in content
        assert "2025" in content
        assert "SINGLE" in content
        assert "150000.00" in content
        assert "BALANCE DUE" in content

    def test_render_refund(self) -> None:
        estimate = TaxEstimate(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("50000"),
            interest_income=Decimal("0"),
            dividend_income=Decimal("0"),
            qualified_dividends=Decimal("0"),
            short_term_gains=Decimal("0"),
            long_term_gains=Decimal("0"),
            total_income=Decimal("50000"),
            agi=Decimal("50000"),
            standard_deduction=Decimal("15700"),
            deduction_used=Decimal("15700"),
            taxable_income=Decimal("34300"),
            federal_regular_tax=Decimal("4000"),
            federal_ltcg_tax=Decimal("0"),
            federal_niit=Decimal("0"),
            federal_amt=Decimal("0"),
            federal_total_tax=Decimal("4000"),
            federal_withheld=Decimal("6000"),
            federal_balance_due=Decimal("-2000"),
            ca_taxable_income=Decimal("34300"),
            ca_tax=Decimal("1500"),
            ca_mental_health_tax=Decimal("0"),
            ca_total_tax=Decimal("1500"),
            ca_withheld=Decimal("2000"),
            ca_balance_due=Decimal("-500"),
            total_tax=Decimal("5500"),
            total_withheld=Decimal("8000"),
            total_balance_due=Decimal("-2500"),
        )
        gen = TaxSummaryGenerator()
        content = gen.render(estimate)
        assert "REFUND" in content

    def test_render_with_amt_credit(self) -> None:
        estimate = TaxEstimate(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("200000"),
            interest_income=Decimal("0"),
            dividend_income=Decimal("0"),
            qualified_dividends=Decimal("0"),
            short_term_gains=Decimal("0"),
            long_term_gains=Decimal("0"),
            total_income=Decimal("200000"),
            agi=Decimal("200000"),
            standard_deduction=Decimal("15700"),
            deduction_used=Decimal("15700"),
            taxable_income=Decimal("184300"),
            federal_regular_tax=Decimal("35000"),
            federal_ltcg_tax=Decimal("0"),
            federal_niit=Decimal("0"),
            federal_amt=Decimal("0"),
            additional_medicare_tax=Decimal("500"),
            amt_credit_used=Decimal("2000"),
            federal_total_tax=Decimal("33500"),
            federal_withheld=Decimal("30000"),
            federal_balance_due=Decimal("3500"),
            ca_taxable_income=Decimal("184300"),
            ca_tax=Decimal("15000"),
            ca_mental_health_tax=Decimal("0"),
            ca_total_tax=Decimal("15000"),
            ca_withheld=Decimal("14000"),
            ca_balance_due=Decimal("1000"),
            total_tax=Decimal("48500"),
            total_withheld=Decimal("44000"),
            total_balance_due=Decimal("4500"),
        )
        gen = TaxSummaryGenerator()
        content = gen.render(estimate)
        assert "AMT Credit" in content
        assert "Addl Medicare Tax" in content


# ---------------------------------------------------------------------------
# CLI Report Command Tests
# ---------------------------------------------------------------------------

class TestReportCLI:
    """Tests for the CLI report command."""

    def test_report_generates_files(self, populated_db: Path, tmp_path: Path) -> None:
        """Test that the report command generates expected output files."""
        output_dir = tmp_path / "output_reports"
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "report",
                "2025",
                "--output", str(output_dir),
                "--db", str(populated_db),
            ],
        )
        assert result.exit_code == 0, f"CLI failed: {result.output}"
        assert "Generating reports" in result.output

        # Should have Form 8949 and Reconciliation (we have sale results)
        assert (output_dir / "2025_form8949.txt").exists()
        assert (output_dir / "2025_reconciliation.txt").exists()

        # Tax summary should always be generated
        assert (output_dir / "2025_tax_summary.txt").exists()

        # Verify content
        form8949_content = (output_dir / "2025_form8949.txt").read_text()
        assert "FORM 8949" in form8949_content
        assert "ACME" in form8949_content

        recon_content = (output_dir / "2025_reconciliation.txt").read_text()
        assert "Reconciliation" in recon_content

        summary_content = (output_dir / "2025_tax_summary.txt").read_text()
        assert "Tax Estimate Summary" in summary_content

    def test_report_no_db(self, tmp_path: Path) -> None:
        """Test error when database doesn't exist."""
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "report",
                "2025",
                "--db", str(tmp_path / "nonexistent.db"),
            ],
        )
        assert result.exit_code == 1
        assert "No database found" in result.output

    def test_report_invalid_filing_status(self, populated_db: Path, tmp_path: Path) -> None:
        """Test error with invalid filing status."""
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "report",
                "2025",
                "--db", str(populated_db),
                "--filing-status", "INVALID",
            ],
        )
        assert result.exit_code == 1
        assert "Invalid filing status" in result.output

    def test_report_creates_output_dir(self, populated_db: Path, tmp_path: Path) -> None:
        """Test that output directory is created if it doesn't exist."""
        output_dir = tmp_path / "new_dir" / "reports"
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "report",
                "2025",
                "--output", str(output_dir),
                "--db", str(populated_db),
            ],
        )
        assert result.exit_code == 0
        assert output_dir.exists()


# ---------------------------------------------------------------------------
# Graceful Missing Data Tests
# ---------------------------------------------------------------------------

class TestReportMissingData:
    """Tests for graceful handling of missing data."""

    def test_no_sales_skips_form8949(self, db_path: Path, tmp_path: Path) -> None:
        """When no sale results exist, Form 8949 and reconciliation are skipped."""
        # Insert only a W-2 (no sales)
        conn = create_schema(db_path)
        repo = TaxRepository(conn)
        batch_id = repo.create_import_batch(
            source="manual", tax_year=2025, file_path="test.json",
            form_type="w2", record_count=1,
        )
        from app.models.tax_forms import W2
        w2 = W2(
            tax_year=2025,
            employer_name="Test Corp",
            box1_wages=Decimal("100000"),
            box2_federal_withheld=Decimal("15000"),
            box16_state_wages=Decimal("100000"),
            box17_state_withheld=Decimal("6000"),
        )
        repo.save_w2(w2, batch_id)
        conn.close()

        output_dir = tmp_path / "output_reports"
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "report",
                "2025",
                "--output", str(output_dir),
                "--db", str(db_path),
            ],
        )
        assert result.exit_code == 0
        assert "skipped (no sale results)" in result.output
        assert not (output_dir / "2025_form8949.txt").exists()
        assert not (output_dir / "2025_reconciliation.txt").exists()
        # Tax summary should still be generated
        assert (output_dir / "2025_tax_summary.txt").exists()

    def test_no_espp_skips_espp_report(self, populated_db: Path, tmp_path: Path) -> None:
        """When no ESPP sales exist, ESPP report is skipped."""
        output_dir = tmp_path / "output_reports"
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "report",
                "2025",
                "--output", str(output_dir),
                "--db", str(populated_db),
            ],
        )
        assert result.exit_code == 0
        assert "skipped (no ESPP sales)" in result.output
        assert not (output_dir / "2025_espp_income.txt").exists()

    def test_no_iso_skips_amt_worksheet(self, populated_db: Path, tmp_path: Path) -> None:
        """When no ISO exercises exist, AMT worksheet is skipped."""
        output_dir = tmp_path / "output_reports"
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "report",
                "2025",
                "--output", str(output_dir),
                "--db", str(populated_db),
            ],
        )
        assert result.exit_code == 0
        assert "skipped (no ISO exercises)" in result.output
        assert not (output_dir / "2025_amt_worksheet.txt").exists()
