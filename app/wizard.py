"""Interactive step-by-step wizard for TaxBot 9000.

Guides the user through the full tax processing pipeline:
  Phase 0 — Welcome & setup (tax year, filing status, DB init)
  Phase 1 — Import data (loop: source, file, validate, save)
  Phase 2 — Reconcile (automatic basis correction)
  Phase 3 — Estimate (deductions, carryovers, payments → tax due)
  Phase 4 — Strategy (optional tax-reduction analysis)
  Phase 5 — Reports (optional file generation)
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.rule import Rule
from rich.table import Table

from app.cli import MASCOT
from app.models.enums import FilingStatus

# ---------------------------------------------------------------------------
# Filing-status helpers
# ---------------------------------------------------------------------------

_FS_MAP: dict[str, str] = {
    "SINGLE": "SINGLE",
    "MFJ": "MARRIED_FILING_JOINTLY",
    "MFS": "MARRIED_FILING_SEPARATELY",
    "HOH": "HEAD_OF_HOUSEHOLD",
}

_FS_CHOICES = list(_FS_MAP.keys())


def _filing_status_to_enum(key: str) -> FilingStatus:
    """Convert a short filing-status key (e.g. 'MFJ') to a FilingStatus enum."""
    return FilingStatus(_FS_MAP[key.upper()])


# ---------------------------------------------------------------------------
# Source / extension helpers
# ---------------------------------------------------------------------------

_EXPECTED_EXT: dict[str, str] = {
    "pdf": ".pdf",
    "manual": ".json",
    "shareworks": ".pdf",
    "robinhood": ".csv",
}

_SOURCE_CHOICES = list(_EXPECTED_EXT.keys())


def _expected_extension(source: str) -> str:
    """Return the expected file extension for *source*."""
    return _EXPECTED_EXT[source.lower()]


# ---------------------------------------------------------------------------
# Decimal prompt helper
# ---------------------------------------------------------------------------


def _prompt_decimal(
    label: str,
    default: Decimal,
    console: Console,
) -> Decimal:
    """Prompt the user for a Decimal value, retrying on bad input."""
    while True:
        raw = Prompt.ask(
            label,
            default=str(default),
            console=console,
        )
        try:
            return Decimal(raw)
        except InvalidOperation:
            console.print(f"[red]Invalid number: {raw!r}. Try again.[/red]")


# ---------------------------------------------------------------------------
# Phase header
# ---------------------------------------------------------------------------


def _show_phase_header(phase_num: int, title: str, console: Console) -> None:
    console.print()
    console.print(Rule(f"Phase {phase_num}: {title}", style="bold cyan"))
    console.print()


# ---------------------------------------------------------------------------
# Phase 0 — Welcome & Setup
# ---------------------------------------------------------------------------


def _phase_setup(
    console: Console,
    db_path: Path,
) -> tuple[int, FilingStatus, Path, object, sqlite3.Connection]:
    """Welcome panel, year/filing-status prompts, DB init."""
    from app.db.repository import TaxRepository
    from app.db.schema import create_schema

    console.print(
        Panel(
            f"[bold green]{MASCOT}[/bold green]\n"
            "[bold]Interactive Tax Processing Wizard[/bold]\n\n"
            "This wizard will guide you through:\n"
            "  1. Importing tax documents\n"
            "  2. Reconciling cost basis\n"
            "  3. Estimating your tax liability\n"
            "  4. Analyzing tax strategies\n"
            "  5. Generating reports",
            title="[bold cyan]TaxBot 9000 Wizard[/bold cyan]",
            border_style="cyan",
        )
    )

    year = IntPrompt.ask("Tax year", default=2024, console=console)

    fs_key = Prompt.ask(
        "Filing status",
        choices=_FS_CHOICES,
        default="SINGLE",
        console=console,
    )
    filing_status = _filing_status_to_enum(fs_key)

    # Initialize DB
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = create_schema(db_path)
    repo = TaxRepository(conn)

    console.print(f"\n[dim]Database: {db_path}[/dim]")
    return year, filing_status, db_path, repo, conn


# ---------------------------------------------------------------------------
# PDF parse-and-import helper
# ---------------------------------------------------------------------------


def _parse_and_import_pdf(
    file_path: Path,
    year: int,
    repo: object,
    conn: sqlite3.Connection,
    console: Console,
) -> dict:
    """Parse a raw PDF tax form and import the result in one step.

    Runs the same pipeline as ``cli.py parse`` inline, writes parsed JSON
    to a temp file, then imports via :func:`_execute_import` with the
    ``manual`` adapter.

    Returns the import summary dict (same shape as :func:`_execute_import`).
    """
    import json
    import os
    import tempfile

    import pdfplumber

    from app.parsing.detector import detect_form_type
    from app.parsing.extractors import get_extractor
    from app.parsing.redactor import Redactor

    # 1. Extract text + tables from PDF
    all_text = ""
    all_tables: list[list[list[str]]] = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            all_text += page_text + "\n"
            page_tables = page.extract_tables() or []
            all_tables.extend(page_tables)

    # 2. Redact PII
    redactor = Redactor()
    redaction_result = redactor.redact(all_text)
    redacted_text = redaction_result.text

    # 3. Detect form type and extract data
    detected_type = detect_form_type(redacted_text)
    used_vision = False

    # Fallback to Vision API if text-based detection fails
    if detected_type is None:
        if os.environ.get("ANTHROPIC_API_KEY"):
            from app.parsing.vision import VisionExtractor

            console.print("[dim]Text detection failed — trying Vision API...[/dim]")
            extractor_v = VisionExtractor()
            images = extractor_v.pdf_to_images(file_path)
            detected_type = extractor_v.detect_form_type(images)
            used_vision = True

    if detected_type is None:
        raise ValueError(
            "Could not detect form type. Ensure the PDF is a supported tax form "
            "(W-2, 1099-B, 1099-DIV, 1099-INT, 3921, 3922)."
        )

    console.print(f"[bold]Detected form:[/bold] {detected_type.value}")

    # 4. Extract structured data
    if used_vision:
        # Text was unreadable — use Vision for extraction too
        console.print("[dim]Extracting fields via Vision API...[/dim]")
        data = extractor_v.extract(images, detected_type)
    else:
        extractor = get_extractor(detected_type)
        data = extractor.extract(redacted_text, all_tables)

    # 5. Validate using the regex extractor's shared validation logic
    regex_extractor = get_extractor(detected_type)
    errors = regex_extractor.validate_extraction(data)
    if errors:
        raise ValueError(
            "Extraction errors:\n" + "\n".join(f"  - {e}" for e in errors)
        )

    # Show warnings (non-blocking)
    warnings = regex_extractor.get_warnings(data)
    for w in warnings:
        console.print(f"[yellow]Warning: {w}[/yellow]")

    # 6. Scrub PII from output
    if isinstance(data, dict):
        data = redactor.scrub_output(data)
    elif isinstance(data, list):
        data = [redactor.scrub_output(record) for record in data]

    # 7. Override tax year from wizard
    if isinstance(data, list):
        for record in data:
            record["tax_year"] = year
    else:
        data["tax_year"] = year

    # 8. Write to temp JSON file and import via ManualAdapter
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False,
    ) as tmp:
        json.dump(data, tmp, default=str)
        tmp_path = Path(tmp.name)

    try:
        summary = _execute_import("manual", tmp_path, year, repo, conn, console)
        # Override source to reflect the actual origin
        summary["source"] = "pdf"
        summary["file"] = str(file_path)
        return summary
    finally:
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Phase 1 — Import Data (loop)
# ---------------------------------------------------------------------------


def _execute_import(
    source: str,
    file_path: Path,
    year: int,
    repo: object,
    conn: sqlite3.Connection,
    console: Console,
) -> dict:
    """Run a single import and return a summary dict.

    Returns ``{"source": ..., "file": ..., "forms": N, ...}``.
    Raises on validation/parse errors so the caller can catch and retry.
    """
    from app.db.repository import TaxRepository
    from app.models.tax_forms import W2, Form1099DIV, Form1099INT

    assert isinstance(repo, TaxRepository)

    if source == "shareworks":
        from app.ingestion.shareworks import ShareworksAdapter
        adapter = ShareworksAdapter()
    elif source == "robinhood":
        from app.ingestion.robinhood import RobinhoodAdapter
        adapter = RobinhoodAdapter()
    else:
        from app.ingestion.manual import ManualAdapter
        adapter = ManualAdapter()

    result = adapter.parse(file_path)
    result.tax_year = year
    for form in result.forms:
        form.tax_year = year

    errors = adapter.validate(result)
    if errors:
        raise ValueError("Validation errors:\n" + "\n".join(f"  - {e}" for e in errors))

    # Duplicate warnings (non-blocking)
    for form in result.forms:
        if isinstance(form, W2) and repo.check_w2_duplicate(form.employer_name, form.tax_year):
            console.print(
                f"[yellow]Warning: W-2 from {form.employer_name} ({form.tax_year}) already imported[/yellow]"
            )
    for event in result.events:
        if repo.check_event_duplicate(
            event.event_type.value,
            event.event_date.isoformat(),
            str(event.shares),
        ):
            console.print(
                f"[yellow]Warning: {event.event_type.value} event on "
                f"{event.event_date} ({event.shares} shares) may be a duplicate[/yellow]"
            )

    record_count = len(result.forms) + len(result.events) + len(result.sales)
    batch_id = repo.create_import_batch(
        source=source,
        tax_year=result.tax_year,
        file_path=str(file_path),
        form_type=result.form_type.value,
        record_count=record_count,
    )

    for form in result.forms:
        if isinstance(form, W2):
            repo.save_w2(form, batch_id)
        elif isinstance(form, Form1099DIV):
            repo.save_1099div(form, batch_id)
        elif isinstance(form, Form1099INT):
            repo.save_1099int(form, batch_id)
    for event in result.events:
        repo.save_event(event, batch_id)
    for lot in result.lots:
        repo.save_lot(lot, batch_id)
    for sale in result.sales:
        repo.save_sale(sale, batch_id)

    return {
        "source": source,
        "file": str(file_path),
        "form_type": result.form_type.value,
        "forms": len(result.forms),
        "events": len(result.events),
        "lots": len(result.lots),
        "sales": len(result.sales),
    }


def _phase_import(
    year: int,
    db_path: Path,
    repo: object,
    conn: sqlite3.Connection,
    console: Console,
) -> list[dict]:
    """Prompt-driven import loop. Returns list of import summary dicts."""
    _show_phase_header(1, "Import Data", console)

    imports: list[dict] = []

    while True:
        source = Prompt.ask(
            "Data source",
            choices=_SOURCE_CHOICES,
            console=console,
        )

        ext = _expected_extension(source)
        file_str = Prompt.ask(
            f"File path ({ext})",
            console=console,
        )
        file_path = Path(file_str.strip().strip("'\"")).expanduser()

        # Validate existence and extension
        if not file_path.exists():
            console.print(f"[red]File not found: {file_path}[/red]")
            if Confirm.ask("Try another file?", default=True, console=console):
                continue
            break

        if file_path.suffix.lower() != ext:
            console.print(
                f"[red]Expected {ext} file for {source}, got {file_path.suffix}[/red]"
            )
            if Confirm.ask("Try another file?", default=True, console=console):
                continue
            break

        # Execute import
        try:
            if source == "pdf":
                summary = _parse_and_import_pdf(file_path, year, repo, conn, console)
            else:
                summary = _execute_import(source, file_path, year, repo, conn, console)
            imports.append(summary)

            # Display summary table
            tbl = Table(title="Import Summary", show_header=True)
            tbl.add_column("Field", style="cyan")
            tbl.add_column("Value", style="green")
            tbl.add_row("Source", summary["source"])
            tbl.add_row("Form type", summary["form_type"])
            tbl.add_row("Forms", str(summary["forms"]))
            tbl.add_row("Events", str(summary["events"]))
            tbl.add_row("Lots", str(summary["lots"]))
            tbl.add_row("Sales", str(summary["sales"]))
            console.print(tbl)

        except Exception as exc:
            console.print(f"[red]Import error: {exc}[/red]")
            if Confirm.ask("Try again?", default=True, console=console):
                continue
            break

        if not Confirm.ask("Import another file?", default=False, console=console):
            break

    return imports


# ---------------------------------------------------------------------------
# Phase 2 — Reconcile
# ---------------------------------------------------------------------------


def _phase_reconcile(
    year: int,
    repo: object,
    console: Console,
) -> dict | None:
    """Run reconciliation engine. Returns run summary dict or None."""
    from app.db.repository import TaxRepository
    from app.engines.reconciliation import ReconciliationEngine

    _show_phase_header(2, "Reconcile", console)
    assert isinstance(repo, TaxRepository)

    engine = ReconciliationEngine(repo)
    console.print("Running basis correction and sale-to-lot matching...")

    run = engine.reconcile(year)

    def _fmt(val: str) -> str:
        return f"{Decimal(val):,.2f}"

    tbl = Table(title="Reconciliation Results", show_header=True)
    tbl.add_column("Metric", style="cyan")
    tbl.add_column("Value", style="green", justify="right")
    tbl.add_row("Total sales", str(run["total_sales"]))
    tbl.add_row("Matched", str(run["matched_sales"]))
    passthrough = run.get("passthrough_sales", 0)
    if passthrough:
        tbl.add_row("Pass-through", str(passthrough))
    tbl.add_row("Unmatched", str(run["unmatched_sales"]))
    tbl.add_row("Total proceeds", f"${_fmt(run.get('total_proceeds', '0'))}")
    tbl.add_row("Correct basis", f"${_fmt(run.get('total_correct_basis', '0'))}")
    tbl.add_row("Gain/Loss", f"${_fmt(run.get('total_gain_loss', '0'))}")

    ordinary = run.get("total_ordinary_income", "0")
    if ordinary and ordinary != "0":
        tbl.add_row("Ordinary income", f"${_fmt(ordinary)}")
    amt = run.get("total_amt_adjustment", "0")
    if amt and amt != "0":
        tbl.add_row("AMT adjustment", f"${_fmt(amt)}")

    console.print(tbl)

    if run.get("warnings"):
        for w in run["warnings"]:
            console.print(f"[yellow]Warning: {w}[/yellow]")
    if run.get("errors"):
        for e in run["errors"]:
            console.print(f"[red]Error: {e}[/red]")

    return run


# ---------------------------------------------------------------------------
# Phase 3 — Estimate
# ---------------------------------------------------------------------------


def _display_estimate(result: object, console: Console) -> None:
    """Pretty-print a TaxEstimate using Rich."""
    from app.models.reports import TaxEstimate

    assert isinstance(result, TaxEstimate)

    # Income table
    inc = Table(title="Income", show_header=False, padding=(0, 1))
    inc.add_column("", style="cyan", min_width=28)
    inc.add_column("", justify="right", style="green")
    inc.add_row("W-2 Wages", f"${result.w2_wages:,.2f}")
    inc.add_row("Interest Income", f"${result.interest_income:,.2f}")
    inc.add_row("Dividend Income", f"${result.dividend_income:,.2f}")
    inc.add_row("  (Qualified)", f"${result.qualified_dividends:,.2f}")
    inc.add_row("Short-Term Gains", f"${result.short_term_gains:,.2f}")
    inc.add_row("Long-Term Gains", f"${result.long_term_gains:,.2f}")
    inc.add_row("Total Income", f"${result.total_income:,.2f}")
    inc.add_row("AGI", f"${result.agi:,.2f}")
    console.print(inc)

    # Federal tax table
    fed = Table(title="Federal Tax", show_header=False, padding=(0, 1))
    fed.add_column("", style="cyan", min_width=28)
    fed.add_column("", justify="right", style="green")
    fed.add_row("Deduction Used", f"${result.deduction_used:,.2f}")
    fed.add_row("Taxable Income", f"${result.taxable_income:,.2f}")
    fed.add_row("Ordinary Income Tax", f"${result.federal_regular_tax:,.2f}")
    fed.add_row("LTCG/QDiv Tax", f"${result.federal_ltcg_tax:,.2f}")
    fed.add_row("NIIT (3.8%)", f"${result.federal_niit:,.2f}")
    fed.add_row("AMT", f"${result.federal_amt:,.2f}")
    if result.additional_medicare_tax > 0:
        fed.add_row("Addl Medicare Tax", f"${result.additional_medicare_tax:,.2f}")
    if result.amt_credit_used > 0:
        fed.add_row("AMT Credit (8801)", f"-${result.amt_credit_used:,.2f}")
    fed.add_row("Total Federal Tax", f"${result.federal_total_tax:,.2f}")
    fed.add_row("Federal Withheld", f"${result.federal_withheld:,.2f}")
    if result.federal_estimated_payments > 0:
        fed.add_row("Est. Payments", f"${result.federal_estimated_payments:,.2f}")
    fed.add_row("Federal Balance Due", f"${result.federal_balance_due:,.2f}")
    console.print(fed)

    # California tax table
    ca = Table(title="California Tax", show_header=False, padding=(0, 1))
    ca.add_column("", style="cyan", min_width=28)
    ca.add_column("", justify="right", style="green")
    ca.add_row("CA Taxable Income", f"${result.ca_taxable_income:,.2f}")
    ca.add_row("CA Income Tax", f"${result.ca_tax:,.2f}")
    ca.add_row("Mental Health Tax", f"${result.ca_mental_health_tax:,.2f}")
    ca.add_row("Total CA Tax", f"${result.ca_total_tax:,.2f}")
    ca.add_row("CA Withheld", f"${result.ca_withheld:,.2f}")
    if result.ca_estimated_payments > 0:
        ca.add_row("Est. Payments", f"${result.ca_estimated_payments:,.2f}")
    ca.add_row("CA Balance Due", f"${result.ca_balance_due:,.2f}")
    console.print(ca)

    # Total summary
    style = "bold red" if result.total_balance_due > 0 else "bold green"
    label = "BALANCE DUE" if result.total_balance_due > 0 else "REFUND"
    amount = abs(result.total_balance_due)
    console.print(
        Panel(
            f"[bold]Total Tax:[/bold] ${result.total_tax:,.2f}\n"
            f"[bold]Total Withheld:[/bold] ${result.total_withheld:,.2f}\n"
            f"[{style}]{label}: ${amount:,.2f}[/{style}]",
            title="[bold]Summary[/bold]",
            border_style="cyan",
        )
    )


def _phase_estimate(
    year: int,
    filing_status: FilingStatus,
    repo: object,
    console: Console,
) -> tuple[object | None, dict]:
    """Prompt for deduction inputs, run estimator. Returns (TaxEstimate, deduction_inputs)."""
    from app.db.repository import TaxRepository
    from app.engines.estimator import TaxEstimator
    from app.models.deductions import ItemizedDeductions

    _show_phase_header(3, "Estimate Tax", console)
    assert isinstance(repo, TaxRepository)

    # Estimated payments
    console.print("[bold]Estimated Payments[/bold]")
    fed_est = _prompt_decimal("  Federal estimated payments", Decimal("0"), console)
    state_est = _prompt_decimal("  State estimated payments", Decimal("0"), console)

    # Itemized deductions
    console.print("\n[bold]Itemized Deductions (Schedule A)[/bold]")
    salt = _prompt_decimal("  SALT (state/local tax paid)", Decimal("0"), console)
    property_tax = _prompt_decimal("  Property tax", Decimal("0"), console)
    mortgage = _prompt_decimal("  Mortgage interest", Decimal("0"), console)
    charitable = _prompt_decimal("  Charitable contributions", Decimal("0"), console)
    medical = _prompt_decimal("  Medical expenses", Decimal("0"), console)

    # Carryovers
    console.print("\n[bold]Prior Year Carryovers[/bold]")
    st_co = _prompt_decimal("  ST capital loss carryover", Decimal("0"), console)
    lt_co = _prompt_decimal("  LT capital loss carryover", Decimal("0"), console)
    amt_credit = _prompt_decimal("  AMT credit carryforward", Decimal("0"), console)

    itemized_detail = ItemizedDeductions(
        state_income_tax_paid=salt,
        real_estate_taxes=property_tax,
        mortgage_interest=mortgage,
        charitable_cash=charitable,
        medical_expenses=medical,
    )

    # Capture deduction inputs for strategy phase
    deduction_inputs = {
        "charitable": charitable,
        "property_tax": property_tax,
        "mortgage_interest": mortgage,
    }

    console.print("\nCalculating...")
    engine = TaxEstimator()
    result = engine.estimate_from_db(
        repo=repo,
        tax_year=year,
        filing_status=filing_status,
        federal_estimated_payments=fed_est,
        state_estimated_payments=state_est,
        itemized_detail=itemized_detail,
        st_loss_carryover=st_co,
        lt_loss_carryover=lt_co,
        prior_year_amt_credit=amt_credit,
    )

    _display_estimate(result, console)

    if engine.warnings:
        for w in engine.warnings:
            console.print(f"[yellow]Warning: {w}[/yellow]")

    return result, deduction_inputs


# ---------------------------------------------------------------------------
# Phase 4 — Strategy (optional)
# ---------------------------------------------------------------------------


def _phase_strategy(
    year: int,
    filing_status: FilingStatus,
    repo: object,
    console: Console,
    deduction_inputs: dict,
) -> None:
    """Optionally run strategy analysis."""
    from app.db.repository import TaxRepository
    from app.engines.strategy import StrategyEngine, UserInputs

    _show_phase_header(4, "Tax Strategy", console)
    assert isinstance(repo, TaxRepository)

    if not Confirm.ask("Run tax strategy analysis?", default=True, console=console):
        console.print("[dim]Skipped.[/dim]")
        return

    user_inputs = UserInputs(
        annual_charitable_giving=deduction_inputs.get("charitable", Decimal("0")),
        property_tax=deduction_inputs.get("property_tax", Decimal("0")),
        mortgage_interest=deduction_inputs.get("mortgage_interest", Decimal("0")),
    )

    console.print("Analyzing...")
    engine = StrategyEngine()
    report = engine.analyze(
        repo=repo,
        tax_year=year,
        filing_status=filing_status,
        user_inputs=user_inputs,
    )

    if not report.recommendations:
        console.print("[dim]No recommendations generated.[/dim]")
        return

    tbl = Table(title="Top Recommendations", show_header=True)
    tbl.add_column("#", justify="right", style="dim", width=3)
    tbl.add_column("Priority", style="magenta", width=10)
    tbl.add_column("Strategy", style="cyan", min_width=30)
    tbl.add_column("Savings", justify="right", style="green", width=12)

    for i, rec in enumerate(report.recommendations[:10], 1):
        savings = f"${rec.estimated_savings:,.0f}" if rec.estimated_savings > 0 else "(info)"
        tbl.add_row(str(i), rec.priority.value, rec.name, savings)

    console.print(tbl)

    # Show details for each recommendation
    for i, rec in enumerate(report.recommendations[:10], 1):
        console.print(f"\n[bold][{i}] {rec.name}[/bold]")
        console.print(f"  Situation: {rec.situation}")
        console.print(f"  Impact: {rec.quantified_impact}")
        if rec.action_steps:
            for step in rec.action_steps:
                console.print(f"    - {step}")


# ---------------------------------------------------------------------------
# Phase 5 — Reports (optional)
# ---------------------------------------------------------------------------


def _phase_reports(
    year: int,
    filing_status: FilingStatus,
    repo: object,
    console: Console,
) -> None:
    """Optionally generate report files."""
    from datetime import date as date_type

    from app.db.repository import TaxRepository
    from app.engines.estimator import TaxEstimator
    from app.engines.strategy import StrategyEngine, UserInputs
    from app.models.enums import (
        AdjustmentCode,
        DispositionType,
        Form8949Category,
        HoldingPeriod,
    )
    from app.models.equity_event import SaleResult, Security
    from app.models.reports import AMTWorksheetLine, ESPPIncomeLine, ReconciliationLine
    from app.reports import (
        AMTWorksheetGenerator,
        ESPPReportGenerator,
        Form8949Generator,
        ReconciliationReportGenerator,
        StrategyReportGenerator,
        TaxSummaryGenerator,
    )

    _show_phase_header(5, "Generate Reports", console)
    assert isinstance(repo, TaxRepository)

    if not Confirm.ask("Generate report files?", default=True, console=console):
        console.print("[dim]Skipped.[/dim]")
        return

    output_str = Prompt.ask("Output directory", default="reports/", console=console)
    output = Path(output_str)
    output.mkdir(parents=True, exist_ok=True)

    generated: list[str] = []

    # Load common data
    sale_result_rows = repo.get_sale_results(year)
    sale_rows = repo.get_sales(year)
    sale_map = {s["id"]: s for s in sale_rows}
    lot_rows = repo.get_lots()
    lot_map = {lt["id"]: lt for lt in lot_rows}

    def _build_sale_result(row: dict) -> SaleResult:
        sale_row = sale_map.get(row["sale_id"], {})
        ticker = sale_row.get("ticker", "UNKNOWN")
        sec_name = sale_row.get("security_name", ticker)
        return SaleResult(
            sale_id=row["sale_id"],
            lot_id=row.get("lot_id"),
            security=Security(ticker=ticker, name=sec_name),
            acquisition_date=date_type.fromisoformat(row["acquisition_date"]),
            sale_date=date_type.fromisoformat(row["sale_date"]),
            shares=Decimal(str(row["shares"])),
            proceeds=Decimal(str(row["proceeds"])),
            broker_reported_basis=(
                Decimal(str(row["broker_reported_basis"]))
                if row.get("broker_reported_basis") is not None
                else None
            ),
            correct_basis=Decimal(str(row["correct_basis"])),
            adjustment_amount=Decimal(str(row["adjustment_amount"])),
            adjustment_code=AdjustmentCode(row["adjustment_code"]),
            holding_period=HoldingPeriod(row["holding_period"]),
            form_8949_category=Form8949Category(row["form_8949_category"]),
            gain_loss=Decimal(str(row["gain_loss"])),
            ordinary_income=Decimal(str(row.get("ordinary_income", "0"))),
            amt_adjustment=Decimal(str(row.get("amt_adjustment", "0"))),
            wash_sale_disallowed=Decimal(str(row.get("wash_sale_disallowed", "0"))),
            notes=row.get("notes"),
        )

    # 1. Form 8949
    if sale_result_rows:
        try:
            sale_results = [_build_sale_result(r) for r in sale_result_rows]
            gen = Form8949Generator()
            lines = gen.generate_lines(sale_results)
            content = gen.render(lines)
            path = output / f"{year}_form8949.txt"
            path.write_text(content)
            generated.append(str(path))
        except Exception as exc:
            console.print(f"[red]Form 8949 failed: {exc}[/red]")

    # 2. Reconciliation
    if sale_result_rows:
        try:
            recon_lines: list[ReconciliationLine] = []
            for row in sale_result_rows:
                sale_row = sale_map.get(row["sale_id"], {})
                ticker = sale_row.get("ticker", "UNKNOWN")
                broker_basis = (
                    Decimal(str(row["broker_reported_basis"]))
                    if row.get("broker_reported_basis") is not None
                    else None
                )
                correct_basis = Decimal(str(row["correct_basis"]))
                proceeds = Decimal(str(row["proceeds"]))
                gain_loss_correct = Decimal(str(row["gain_loss"]))
                gain_loss_broker = (proceeds - broker_basis) if broker_basis is not None else None
                difference = (
                    (gain_loss_correct - gain_loss_broker)
                    if gain_loss_broker is not None
                    else gain_loss_correct
                )
                recon_lines.append(ReconciliationLine(
                    sale_id=row["sale_id"],
                    security=ticker,
                    sale_date=date_type.fromisoformat(row["sale_date"]),
                    shares=Decimal(str(row["shares"])),
                    broker_proceeds=proceeds,
                    broker_basis=broker_basis,
                    correct_basis=correct_basis,
                    adjustment=Decimal(str(row["adjustment_amount"])),
                    adjustment_code=AdjustmentCode(row["adjustment_code"]),
                    gain_loss_broker=gain_loss_broker,
                    gain_loss_correct=gain_loss_correct,
                    difference=difference,
                    notes=row.get("notes"),
                ))
            gen_recon = ReconciliationReportGenerator()
            content = gen_recon.render(recon_lines)
            path = output / f"{year}_reconciliation.txt"
            path.write_text(content)
            generated.append(str(path))
        except Exception as exc:
            console.print(f"[red]Reconciliation report failed: {exc}[/red]")

    # 3. ESPP Income
    espp_rows = [
        r for r in sale_result_rows
        if lot_map.get(r.get("lot_id"), {}).get("equity_type") == "ESPP"
    ]
    if espp_rows:
        try:
            espp_lines: list[ESPPIncomeLine] = []
            events = repo.get_events(equity_type="ESPP")
            event_map: dict[str, dict] = {}
            for ev in events:
                key = ev.get("ticker", "")
                if key not in event_map:
                    event_map[key] = ev

            for row in espp_rows:
                lot = lot_map.get(row.get("lot_id"), {})
                ticker = lot.get("ticker", "UNKNOWN")
                acq_date = date_type.fromisoformat(row["acquisition_date"])
                sale_date = date_type.fromisoformat(row["sale_date"])
                ev = event_map.get(ticker, {})
                offering_date = (
                    date_type.fromisoformat(ev["offering_date"])
                    if ev.get("offering_date")
                    else acq_date
                )
                purchase_date = acq_date
                fmv_at_purchase = Decimal(str(ev.get("price_per_share", "0")))
                fmv_at_offering = Decimal(str(ev.get("fmv_on_offering_date", "0")))
                purchase_price = Decimal(str(ev.get("purchase_price", "0")))
                proceeds = Decimal(str(row["proceeds"]))
                ordinary_income = Decimal(str(row.get("ordinary_income", "0")))
                correct_basis = Decimal(str(row["correct_basis"]))
                capital_gain_loss = Decimal(str(row["gain_loss"]))
                holding = HoldingPeriod(row["holding_period"])

                def _add_years(d: date_type, years: int) -> date_type:
                    try:
                        return d.replace(year=d.year + years)
                    except ValueError:
                        return d.replace(year=d.year + years, day=28)

                two_years = _add_years(offering_date, 2)
                one_year = _add_years(purchase_date, 1)
                if sale_date >= two_years and sale_date >= one_year:
                    disp_type = DispositionType.QUALIFYING
                else:
                    disp_type = DispositionType.DISQUALIFYING

                espp_lines.append(ESPPIncomeLine(
                    security=ticker,
                    offering_date=offering_date,
                    purchase_date=purchase_date,
                    sale_date=sale_date,
                    shares=Decimal(str(row["shares"])),
                    purchase_price=purchase_price,
                    fmv_at_purchase=fmv_at_purchase,
                    fmv_at_offering=fmv_at_offering,
                    sale_proceeds=proceeds,
                    disposition_type=disp_type,
                    ordinary_income=ordinary_income,
                    adjusted_basis=correct_basis,
                    capital_gain_loss=capital_gain_loss,
                    holding_period=holding,
                ))

            gen_espp = ESPPReportGenerator()
            content = gen_espp.render(espp_lines)
            path = output / f"{year}_espp_income.txt"
            path.write_text(content)
            generated.append(str(path))
        except Exception as exc:
            console.print(f"[red]ESPP report failed: {exc}[/red]")

    # 4. AMT Worksheet
    iso_events = repo.get_events(equity_type="ISO")
    iso_exercises = [ev for ev in iso_events if ev.get("event_type") == "EXERCISE"]
    if iso_exercises:
        try:
            amt_lines: list[AMTWorksheetLine] = []
            for ev in iso_exercises:
                ticker = ev.get("ticker", "UNKNOWN")
                shares = Decimal(str(ev["shares"]))
                strike = Decimal(str(ev.get("strike_price", "0")))
                fmv = Decimal(str(ev["price_per_share"]))
                spread = fmv - strike
                total_pref = spread * shares
                grant_date = (
                    date_type.fromisoformat(ev["grant_date"])
                    if ev.get("grant_date")
                    else date_type.fromisoformat(ev["event_date"])
                )
                exercise_date = date_type.fromisoformat(ev["event_date"])
                amt_lines.append(AMTWorksheetLine(
                    security=ticker,
                    grant_date=grant_date,
                    exercise_date=exercise_date,
                    shares=shares,
                    strike_price=strike,
                    fmv_at_exercise=fmv,
                    spread_per_share=spread,
                    total_amt_preference=total_pref,
                    regular_basis=strike * shares,
                    amt_basis=fmv * shares,
                ))
            gen_amt = AMTWorksheetGenerator()
            content = gen_amt.render(amt_lines)
            path = output / f"{year}_amt_worksheet.txt"
            path.write_text(content)
            generated.append(str(path))
        except Exception as exc:
            console.print(f"[red]AMT worksheet failed: {exc}[/red]")

    # 5. Strategy Report
    try:
        s_engine = StrategyEngine()
        s_report = s_engine.analyze(
            repo=repo,
            tax_year=year,
            filing_status=filing_status,
            user_inputs=UserInputs(),
        )
        if s_report.recommendations:
            gen_strategy = StrategyReportGenerator()
            content = gen_strategy.render(s_report.recommendations)
            path = output / f"{year}_strategy.txt"
            path.write_text(content)
            generated.append(str(path))
    except Exception as exc:
        console.print(f"[red]Strategy report failed: {exc}[/red]")

    # 6. Tax Summary
    try:
        estimator = TaxEstimator()
        estimate = estimator.estimate_from_db(
            repo=repo,
            tax_year=year,
            filing_status=filing_status,
        )
        gen_summary = TaxSummaryGenerator()
        content = gen_summary.render(estimate)
        path = output / f"{year}_tax_summary.txt"
        path.write_text(content)
        generated.append(str(path))
    except Exception as exc:
        console.print(f"[red]Tax summary failed: {exc}[/red]")

    if generated:
        tbl = Table(title="Reports Generated", show_header=False)
        tbl.add_column("", style="green")
        for p in generated:
            tbl.add_row(f"[green]✓[/green] {p}")
        console.print(tbl)
    else:
        console.print("[dim]No reports generated.[/dim]")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_wizard(db: Path, console: Console | None = None) -> None:
    """Main wizard orchestration — called from cli.py."""
    if console is None:
        console = Console()

    # Phase 0 — Setup
    _show_phase_header(0, "Setup", console)
    year, filing_status, db_path, repo, conn = _phase_setup(console, db)

    try:
        # Phase 1 — Import
        _phase_import(year, db_path, repo, conn, console)

        # Phase 2 — Reconcile
        _phase_reconcile(year, repo, console)

        # Phase 3 — Estimate
        estimate, deduction_inputs = _phase_estimate(year, filing_status, repo, console)

        # Phase 4 — Strategy
        _phase_strategy(year, filing_status, repo, console, deduction_inputs)

        # Phase 5 — Reports
        _phase_reports(year, filing_status, repo, console)

        # Final summary
        console.print()
        console.print(
            Panel(
                f"[bold]Tax Year:[/bold] {year}\n"
                f"[bold]Filing Status:[/bold] {filing_status.value}\n"
                f"[bold]Database:[/bold] {db_path}\n\n"
                "[bold green]Wizard complete![/bold green]",
                title="[bold cyan]TaxBot 9000 — Done[/bold cyan]",
                border_style="cyan",
            )
        )
    finally:
        conn.close()
