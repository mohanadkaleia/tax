"""Typer CLI interface for TaxBot 9000."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import typer

MASCOT = r"""
      _____
     /     \
    | () () |
    |  ___  |
    | |$$$| |
    | |$$$| |
    |  ---  |
     \_____/
    /|     |\
   / |     | \
     |     |
     |     |
    _|  |  |_
   |____|____|

  TaxBot 9000
  "I found $0 basis...again."
"""


def show_mascot() -> None:
    typer.echo(MASCOT)


app = typer.Typer(
    name="taxbot",
    help="TaxBot 9000 — Tax reconciliation for equity compensation.",
)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """TaxBot 9000 — Tax reconciliation for equity compensation."""
    if ctx.invoked_subcommand is None:
        show_mascot()
        raise typer.Exit()


@app.command()
def import_data(
    source: str = typer.Argument(..., help="Data source: manual, shareworks (robinhood coming soon)"),
    file: Path = typer.Argument(..., help="Path to data file (JSON for manual, PDF for shareworks)"),
    year: int | None = typer.Option(
        None,
        "--year",
        "-y",
        help="Tax year (overrides value from JSON if provided)",
    ),
    db: Path = typer.Option(
        Path.home() / ".taxbot" / "taxbot.db",
        "--db",
        help="Path to the SQLite database file",
    ),
) -> None:
    """Import parsed tax data (JSON) into the TaxBot database."""
    # Validate file exists and is JSON
    if not file.exists():
        typer.echo(f"Error: File not found: {file}", err=True)
        raise typer.Exit(1)
    # Validate source
    valid_sources = {"manual", "shareworks", "robinhood"}
    if source.lower() not in valid_sources:
        typer.echo(
            f"Error: Unknown source '{source}'. Valid sources: {', '.join(sorted(valid_sources))}",
            err=True,
        )
        raise typer.Exit(1)

    # Validate file extension based on source
    if source.lower() == "shareworks":
        if file.suffix.lower() != ".pdf":
            typer.echo(f"Error: Shareworks source expects a PDF file: {file}", err=True)
            raise typer.Exit(1)
    elif file.suffix.lower() != ".json":
        typer.echo(f"Error: File must be JSON: {file}", err=True)
        raise typer.Exit(1)

    if source.lower() == "robinhood":
        typer.echo(f"Error: '{source}' adapter not yet implemented.", err=True)
        raise typer.Exit(1)

    from app.db.repository import TaxRepository
    from app.db.schema import create_schema
    from app.models.tax_forms import W2, Form1099DIV, Form1099INT

    # Select adapter by source
    if source.lower() == "shareworks":
        from app.ingestion.shareworks import ShareworksAdapter
        adapter = ShareworksAdapter()
    else:
        from app.ingestion.manual import ManualAdapter
        adapter = ManualAdapter()

    try:
        result = adapter.parse(file)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    # Override tax year if specified
    if year:
        result.tax_year = year
        for form in result.forms:
            form.tax_year = year
        for event in result.events:
            pass  # Events don't have tax_year; date is enough

    # Validate
    errors = adapter.validate(result)
    if errors:
        typer.echo("Validation errors:", err=True)
        for error in errors:
            typer.echo(f"  - {error}", err=True)
        raise typer.Exit(1)

    # Initialize database
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = create_schema(db)
    repo = TaxRepository(conn)

    # Check for duplicates
    duplicates_found = False
    for form in result.forms:
        if isinstance(form, W2) and repo.check_w2_duplicate(form.employer_name, form.tax_year):
            typer.echo(
                f"Warning: W-2 from {form.employer_name} ({form.tax_year}) already imported",
                err=True,
            )
            duplicates_found = True
    for event in result.events:
        if repo.check_event_duplicate(
            event.event_type.value,
            event.event_date.isoformat(),
            str(event.shares),
        ):
            typer.echo(
                f"Warning: {event.event_type.value} event on {event.event_date} "
                f"({event.shares} shares) may be a duplicate",
                err=True,
            )
            duplicates_found = True

    # Create import batch
    record_count = len(result.forms) + len(result.events) + len(result.sales)
    batch_id = repo.create_import_batch(
        source=source,
        tax_year=result.tax_year,
        file_path=str(file),
        form_type=result.form_type.value,
        record_count=record_count,
    )

    # Save all data to database
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

    conn.close()

    # Print summary
    form_type_label = result.form_type.value.upper()
    form_count = len(result.forms)
    event_count = len(result.events)
    lot_count = len(result.lots)
    sale_count = len(result.sales)

    # Build a descriptive summary
    summary_parts = []
    if form_count:
        # Try to get a name from the first form for a nice message
        first = result.forms[0]
        name = (
            getattr(first, "employer_name", None)
            or getattr(first, "broker_name", None)
            or getattr(first, "payer_name", None)
            or ""
        )
        label = f"{form_count} {form_type_label}"
        if name:
            label += f" from {name}"
        summary_parts.append(label)
    if event_count:
        summary_parts.append(f"{event_count} event(s)")
    if lot_count:
        summary_parts.append(f"{lot_count} lot(s)")
    if sale_count:
        summary_parts.append(f"{sale_count} sale(s)")

    typer.echo(f"Imported {', '.join(summary_parts)} (tax year {result.tax_year})")
    if duplicates_found:
        typer.echo("Note: Some records may be duplicates of previously imported data.")


@app.command()
def reconcile(
    year: int = typer.Argument(..., help="Tax year to reconcile"),
    db: Path = typer.Option(
        Path.home() / ".taxbot" / "taxbot.db",
        "--db",
        help="Path to the SQLite database file",
    ),
) -> None:
    """Run basis correction and reconciliation for a tax year."""
    from app.db.repository import TaxRepository
    from app.db.schema import create_schema
    from app.engines.reconciliation import ReconciliationEngine

    if not db.exists():
        typer.echo("Error: No database found. Import data first with `taxbot import-data`.", err=True)
        raise typer.Exit(1)

    conn = create_schema(db)
    repo = TaxRepository(conn)
    engine = ReconciliationEngine(repo)

    typer.echo(f"Reconciling tax year {year}...")
    run = engine.reconcile(year)
    conn.close()

    # Print summary
    typer.echo("\nReconciliation complete:")
    typer.echo(f"  Total sales:     {run['total_sales']}")
    typer.echo(f"  Matched:         {run['matched_sales']}")
    passthrough = run.get('passthrough_sales', 0)
    if passthrough:
        typer.echo(f"  Pass-through:    {passthrough}")
    typer.echo(f"  Unmatched:       {run['unmatched_sales']}")
    from decimal import Decimal
    def _fmt(val: str) -> str:
        """Format a decimal string to 2 decimal places with commas."""
        return f"{Decimal(val):,.2f}"

    typer.echo(f"  Total proceeds:  ${_fmt(run.get('total_proceeds', '0'))}")
    typer.echo(f"  Correct basis:   ${_fmt(run.get('total_correct_basis', '0'))}")
    typer.echo(f"  Gain/Loss:       ${_fmt(run.get('total_gain_loss', '0'))}")

    ordinary = run.get("total_ordinary_income", "0")
    if ordinary and ordinary != "0":
        typer.echo(f"  Ordinary income: ${_fmt(ordinary)}")

    amt = run.get("total_amt_adjustment", "0")
    if amt and amt != "0":
        typer.echo(f"  AMT adjustment:  ${_fmt(amt)}")

    if run.get("warnings"):
        typer.echo("\nWarnings:")
        for w in run["warnings"]:
            typer.echo(f"  - {w}")

    if run.get("errors"):
        typer.echo("\nErrors:")
        for e in run["errors"]:
            typer.echo(f"  - {e}")

    typer.echo(f"\nStatus: {run['status']}")


@app.command()
def estimate(
    year: int = typer.Argument(..., help="Tax year to estimate"),
    filing_status: str = typer.Option(
        "SINGLE",
        "--filing-status",
        "-s",
        help="Filing status: SINGLE, MFJ, MFS, HOH",
    ),
    db: Path = typer.Option(
        Path.home() / ".taxbot" / "taxbot.db",
        "--db",
        help="Path to the SQLite database file",
    ),
    federal_estimated: float = typer.Option(
        0.0,
        "--federal-estimated",
        help="Federal estimated tax payments already made",
    ),
    state_estimated: float = typer.Option(
        0.0,
        "--state-estimated",
        help="State estimated tax payments already made",
    ),
    itemized: float | None = typer.Option(
        None,
        "--itemized",
        help="[Legacy] Total itemized deductions as a single number",
    ),
    deductions_file: Path | None = typer.Option(
        None,
        "--deductions-file",
        help="JSON file with itemized deduction details (Schedule A)",
    ),
    salt: float | None = typer.Option(
        None,
        "--salt",
        help="State/local income tax paid (for SALT deduction)",
    ),
    charitable: float | None = typer.Option(
        None,
        "--charitable",
        help="Charitable contributions (cash)",
    ),
    mortgage_interest: float | None = typer.Option(
        None,
        "--mortgage-interest",
        help="Mortgage interest paid",
    ),
    medical: float | None = typer.Option(
        None,
        "--medical",
        help="Unreimbursed medical/dental expenses",
    ),
    property_tax: float | None = typer.Option(
        None,
        "--property-tax",
        help="Real estate property taxes paid",
    ),
    medicare_wages: float | None = typer.Option(
        None,
        "--medicare-wages",
        help="Medicare wages (W-2 Box 5) — override auto-extraction from DB",
    ),
    medicare_withheld: float | None = typer.Option(
        None,
        "--medicare-withheld",
        help="Medicare tax withheld (W-2 Box 6) — override auto-extraction from DB",
    ),
    st_loss_carryover: float | None = typer.Option(
        None,
        "--st-loss-carryover",
        help="Short-term capital loss carryover from prior year (positive number)",
    ),
    lt_loss_carryover: float | None = typer.Option(
        None,
        "--lt-loss-carryover",
        help="Long-term capital loss carryover from prior year (positive number)",
    ),
    amt_credit_carryover: float | None = typer.Option(
        None,
        "--amt-credit-carryover",
        help="AMT credit carryforward from prior year (Form 8801, deferral items only)",
    ),
) -> None:
    """Compute estimated tax liability for a tax year."""
    import json

    from app.db.repository import TaxRepository
    from app.db.schema import create_schema
    from app.engines.estimator import TaxEstimator
    from app.models.deductions import ItemizedDeductions
    from app.models.enums import FilingStatus

    # Validate filing status
    status_map = {"SINGLE": "SINGLE", "MFJ": "MARRIED_FILING_JOINTLY",
                  "MFS": "MARRIED_FILING_SEPARATELY", "HOH": "HEAD_OF_HOUSEHOLD"}
    fs_key = filing_status.upper()
    fs_value = status_map.get(fs_key, fs_key)
    try:
        fs = FilingStatus(fs_value)
    except ValueError:
        valid = ", ".join(status_map.keys())
        typer.echo(f"Error: Invalid filing status '{filing_status}'. Valid: {valid}", err=True)
        raise typer.Exit(1)

    if not db.exists():
        typer.echo("Error: No database found. Import data first with `taxbot import-data`.", err=True)
        raise typer.Exit(1)

    conn = create_schema(db)
    repo = TaxRepository(conn)
    engine = TaxEstimator()

    fed_est = Decimal(str(federal_estimated))
    state_est = Decimal(str(state_estimated))

    # Build structured itemized deductions from CLI inputs
    itemized_detail = None
    itemized_dec = None

    if deductions_file is not None:
        data = json.loads(deductions_file.read_text())
        itemized_detail = ItemizedDeductions(**data)
    elif any(x is not None for x in [salt, charitable, mortgage_interest, medical, property_tax]):
        itemized_detail = ItemizedDeductions(
            state_income_tax_paid=Decimal(str(salt or 0)),
            charitable_cash=Decimal(str(charitable or 0)),
            mortgage_interest=Decimal(str(mortgage_interest or 0)),
            medical_expenses=Decimal(str(medical or 0)),
            real_estate_taxes=Decimal(str(property_tax or 0)),
        )
    elif itemized is not None:
        itemized_dec = Decimal(str(itemized))

    # Medicare overrides
    mw_override = Decimal(str(medicare_wages)) if medicare_wages is not None else None
    mwh_override = Decimal(str(medicare_withheld)) if medicare_withheld is not None else None

    # Capital loss carryover
    st_co = Decimal(str(st_loss_carryover)) if st_loss_carryover is not None else Decimal("0")
    lt_co = Decimal(str(lt_loss_carryover)) if lt_loss_carryover is not None else Decimal("0")

    # AMT credit carryover
    amt_credit = Decimal(str(amt_credit_carryover)) if amt_credit_carryover is not None else Decimal("0")

    typer.echo(f"Estimating tax for year {year} (filing status: {fs_key})...")
    result = engine.estimate_from_db(
        repo=repo,
        tax_year=year,
        filing_status=fs,
        federal_estimated_payments=fed_est,
        state_estimated_payments=state_est,
        itemized_deductions=itemized_dec,
        itemized_detail=itemized_detail,
        medicare_wages_override=mw_override,
        medicare_tax_withheld_override=mwh_override,
        st_loss_carryover=st_co,
        lt_loss_carryover=lt_co,
        prior_year_amt_credit=amt_credit,
    )
    conn.close()

    typer.echo("")
    typer.echo(f"=== Tax Estimate: {year} ({fs_key}) ===")
    typer.echo("")
    typer.echo("INCOME")
    typer.echo(f"  W-2 Wages:             ${result.w2_wages:>12,.2f}")
    typer.echo(f"  Interest Income:       ${result.interest_income:>12,.2f}")
    typer.echo(f"  Dividend Income:       ${result.dividend_income:>12,.2f}")
    typer.echo(f"    (Qualified:          ${result.qualified_dividends:>12,.2f})")
    typer.echo(f"  Short-Term Gains:      ${result.short_term_gains:>12,.2f}")
    typer.echo(f"  Long-Term Gains:       ${result.long_term_gains:>12,.2f}")
    typer.echo("  ──────────────────────────────────────")
    typer.echo(f"  Total Income:          ${result.total_income:>12,.2f}")
    typer.echo(f"  AGI:                   ${result.agi:>12,.2f}")
    if (
        result.st_loss_carryover_applied > 0
        or result.lt_loss_carryover_applied > 0
        or result.st_loss_carryforward > 0
        or result.lt_loss_carryforward > 0
    ):
        typer.echo("")
        typer.echo("CAPITAL LOSS CARRYOVER")
        if result.st_loss_carryover_applied > 0:
            typer.echo(f"  ST Carryover Applied:  ${result.st_loss_carryover_applied:>12,.2f}")
        if result.lt_loss_carryover_applied > 0:
            typer.echo(f"  LT Carryover Applied:  ${result.lt_loss_carryover_applied:>12,.2f}")
        typer.echo("  ──────────────────────────────────────")
        if result.st_loss_carryforward > 0:
            typer.echo(f"  New ST Carryforward:   ${result.st_loss_carryforward:>12,.2f}")
        if result.lt_loss_carryforward > 0:
            typer.echo(f"  New LT Carryforward:   ${result.lt_loss_carryforward:>12,.2f}")
    typer.echo("")
    typer.echo("DEDUCTIONS")
    detail = result.itemized_detail
    if detail is not None:
        if detail.federal_medical_deduction > 0:
            typer.echo(f"  Medical (after 7.5%):  ${detail.federal_medical_deduction:>12,.2f}")
        typer.echo(f"  SALT (uncapped):       ${detail.federal_salt_uncapped:>12,.2f}")
        typer.echo(f"  SALT (after cap):      ${detail.federal_salt_deduction:>12,.2f}")
        if detail.federal_salt_cap_lost > 0:
            typer.echo(f"    *** ${detail.federal_salt_cap_lost:,.2f} lost to SALT cap")
        if detail.federal_interest_deduction > 0:
            typer.echo(f"  Mortgage Interest:     ${detail.federal_interest_deduction:>12,.2f}")
        typer.echo(f"  Charitable:            ${detail.federal_charitable_deduction:>12,.2f}")
        if detail.federal_casualty_loss > 0 or detail.federal_other_deductions > 0:
            typer.echo(f"  Casualty/Other:        ${detail.federal_casualty_loss + detail.federal_other_deductions:>12,.2f}")
        typer.echo("  ──────────────────────────────────────")
        typer.echo(f"  Federal Itemized:      ${detail.federal_total_itemized:>12,.2f}")
        typer.echo(f"  Standard Deduction:    ${detail.federal_standard_deduction:>12,.2f}")
        label = "ITEMIZED" if detail.federal_used_itemized else "STANDARD"
        typer.echo(f"  >>> Using {label}:      ${detail.federal_deduction_used:>12,.2f}")
        typer.echo("")
        typer.echo(f"  CA Itemized:           ${detail.ca_total_itemized:>12,.2f}")
        typer.echo(f"  CA Standard:           ${detail.ca_standard_deduction:>12,.2f}")
        ca_label = "ITEMIZED" if detail.ca_used_itemized else "STANDARD"
        typer.echo(f"  >>> CA Using {ca_label}: ${detail.ca_deduction_used:>12,.2f}")
    else:
        typer.echo(f"  Standard Deduction:    ${result.standard_deduction:>12,.2f}")
        if result.itemized_deductions:
            typer.echo(f"  Itemized Deductions:   ${result.itemized_deductions:>12,.2f}")
        typer.echo(f"  Deduction Used:        ${result.deduction_used:>12,.2f}")
    if result.section_199a_deduction > 0:
        typer.echo(f"  Section 199A QBI:     -${result.section_199a_deduction:>12,.2f}")
    typer.echo(f"  Taxable Income:        ${result.taxable_income:>12,.2f}")
    typer.echo("")
    typer.echo("FEDERAL TAX")
    typer.echo(f"  Ordinary Income Tax:   ${result.federal_regular_tax:>12,.2f}")
    typer.echo(f"  LTCG/QDiv Tax:         ${result.federal_ltcg_tax:>12,.2f}")
    typer.echo(f"  NIIT (3.8%):           ${result.federal_niit:>12,.2f}")
    typer.echo(f"  AMT:                   ${result.federal_amt:>12,.2f}")
    if result.additional_medicare_tax > 0:
        typer.echo(f"  Addl Medicare Tax:     ${result.additional_medicare_tax:>12,.2f}")
    if result.amt_credit_used > 0:
        typer.echo(f"  AMT Credit (8801):    -${result.amt_credit_used:>12,.2f}")
    if result.federal_foreign_tax_credit > 0:
        typer.echo(f"  Foreign Tax Credit:   -${result.federal_foreign_tax_credit:>12,.2f}")
    typer.echo("  ──────────────────────────────────────")
    typer.echo(f"  Total Federal Tax:     ${result.federal_total_tax:>12,.2f}")
    typer.echo(f"  Federal Withheld:      ${result.federal_withheld:>12,.2f}")
    if result.additional_medicare_withholding_credit > 0:
        typer.echo(f"  Addl Medicare Credit:  ${result.additional_medicare_withholding_credit:>12,.2f}")
    if result.federal_estimated_payments > 0:
        typer.echo(f"  Est. Payments:         ${result.federal_estimated_payments:>12,.2f}")
    typer.echo(f"  Federal Balance Due:   ${result.federal_balance_due:>12,.2f}")
    if result.amt_credit_carryforward > 0:
        typer.echo(f"  AMT Credit Carryforward: ${result.amt_credit_carryforward:>10,.2f}")
    typer.echo("")
    typer.echo("CALIFORNIA TAX")
    typer.echo(f"  CA Taxable Income:     ${result.ca_taxable_income:>12,.2f}")
    typer.echo(f"  CA Income Tax:         ${result.ca_tax:>12,.2f}")
    typer.echo(f"  Mental Health Tax:     ${result.ca_mental_health_tax:>12,.2f}")
    typer.echo("  ──────────────────────────────────────")
    typer.echo(f"  Total CA Tax:          ${result.ca_total_tax:>12,.2f}")
    typer.echo(f"  CA Withheld:           ${result.ca_withheld:>12,.2f}")
    if result.ca_estimated_payments > 0:
        typer.echo(f"  Est. Payments:         ${result.ca_estimated_payments:>12,.2f}")
    typer.echo(f"  CA Balance Due:        ${result.ca_balance_due:>12,.2f}")
    typer.echo("")
    typer.echo("TOTAL")
    typer.echo(f"  Total Tax:             ${result.total_tax:>12,.2f}")
    typer.echo(f"  Total Withheld:        ${result.total_withheld:>12,.2f}")
    typer.echo("  ══════════════════════════════════════")

    if result.total_balance_due > 0:
        typer.echo(f"  BALANCE DUE:           ${result.total_balance_due:>12,.2f}")
    else:
        typer.echo(f"  REFUND:                ${abs(result.total_balance_due):>12,.2f}")

    if engine.warnings:
        typer.echo("")
        typer.echo("WARNINGS:")
        for w in engine.warnings:
            typer.echo(f"  - {w}")


@app.command()
def strategy(
    year: int = typer.Argument(..., help="Tax year for strategy analysis"),
    filing_status: str = typer.Option(
        "SINGLE",
        "--filing-status",
        "-s",
        help="Filing status: SINGLE, MFJ, MFS, HOH",
    ),
    db: Path = typer.Option(
        Path.home() / ".taxbot" / "taxbot.db",
        "--db",
        help="Path to the SQLite database file",
    ),
    age: int | None = typer.Option(None, "--age", help="Taxpayer age (for catch-up contributions)"),
    prices_file: Path | None = typer.Option(
        None, "--prices", help="JSON file with current market prices: {ticker: price}",
    ),
    charitable: float = typer.Option(0, "--charitable", help="Annual charitable giving amount"),
    property_tax: float = typer.Option(0, "--property-tax", help="Annual property tax"),
    mortgage_interest: float = typer.Option(0, "--mortgage-interest", help="Annual mortgage interest"),
    prior_year_tax: float | None = typer.Option(
        None, "--prior-year-tax", help="Prior year total federal tax (for safe harbor)",
    ),
    prior_year_state_tax: float | None = typer.Option(
        None, "--prior-year-state-tax", help="Prior year total CA state tax",
    ),
    amt_credit: float = typer.Option(0, "--amt-credit", help="AMT credit carryforward"),
    loss_carryforward: float = typer.Option(0, "--loss-carryforward", help="Capital loss carryforward"),
    projected_income: float | None = typer.Option(
        None, "--projected-income", help="Projected W-2 income next year",
    ),
    has_hdhp: bool = typer.Option(False, "--hdhp", help="Has high-deductible health plan (HSA eligible)"),
    hsa_coverage: str | None = typer.Option(
        None, "--hsa-coverage", help="HSA coverage type: self or family",
    ),
    iso_grants_file: Path | None = typer.Option(
        None, "--iso-grants",
        help="JSON file with unexercised ISO grants: [{ticker, shares, strike_price, expiration_date}]",
    ),
    nso_grants_file: Path | None = typer.Option(
        None, "--nso-grants",
        help="JSON file with unexercised NSO grants: [{ticker, shares, strike_price, expiration_date}]",
    ),
    future_vests_file: Path | None = typer.Option(
        None, "--future-vests",
        help="JSON file with future RSU vest dates: [{ticker, vest_date, shares}]",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    top_n: int = typer.Option(10, "--top", "-n", help="Show top N recommendations"),
) -> None:
    """Run tax strategy analysis and recommendations."""
    from app.db.repository import TaxRepository
    from app.db.schema import create_schema
    from app.engines.strategy import StrategyEngine, UserInputs
    from app.models.enums import FilingStatus

    # Validate filing status
    status_map = {"SINGLE": "SINGLE", "MFJ": "MARRIED_FILING_JOINTLY",
                  "MFS": "MARRIED_FILING_SEPARATELY", "HOH": "HEAD_OF_HOUSEHOLD"}
    fs_key = filing_status.upper()
    fs_value = status_map.get(fs_key, fs_key)
    try:
        fs = FilingStatus(fs_value)
    except ValueError:
        valid = ", ".join(status_map.keys())
        typer.echo(f"Error: Invalid filing status '{filing_status}'. Valid: {valid}", err=True)
        raise typer.Exit(1)

    if not db.exists():
        typer.echo("Error: No database found. Import data first with `taxbot import-data`.", err=True)
        raise typer.Exit(1)

    conn = create_schema(db)
    repo = TaxRepository(conn)

    # Load market prices from JSON file if provided
    market_prices: dict[str, Decimal] = {}
    if prices_file and prices_file.exists():
        with open(prices_file) as f:
            raw_prices = json.load(f)
            market_prices = {k: Decimal(str(v)) for k, v in raw_prices.items()}

    # Load ISO/NSO grants from JSON files
    iso_grants = None
    if iso_grants_file and iso_grants_file.exists():
        with open(iso_grants_file) as f:
            iso_grants = json.load(f)

    nso_grants = None
    if nso_grants_file and nso_grants_file.exists():
        with open(nso_grants_file) as f:
            nso_grants = json.load(f)

    future_vests = None
    if future_vests_file and future_vests_file.exists():
        with open(future_vests_file) as f:
            future_vests = json.load(f)

    user_inputs = UserInputs(
        age=age,
        has_hdhp=has_hdhp,
        hsa_coverage=hsa_coverage,
        annual_charitable_giving=Decimal(str(charitable)),
        property_tax=Decimal(str(property_tax)),
        mortgage_interest=Decimal(str(mortgage_interest)),
        prior_year_federal_tax=Decimal(str(prior_year_tax)) if prior_year_tax is not None else None,
        prior_year_state_tax=Decimal(str(prior_year_state_tax)) if prior_year_state_tax is not None else None,
        amt_credit_carryforward=Decimal(str(amt_credit)),
        capital_loss_carryforward=Decimal(str(loss_carryforward)),
        projected_income_next_year=Decimal(str(projected_income)) if projected_income is not None else None,
        current_market_prices=market_prices,
        unexercised_iso_grants=iso_grants,
        unexercised_nso_grants=nso_grants,
        future_vest_dates=future_vests,
    )

    typer.echo(f"Analyzing tax strategies for year {year} (filing status: {fs_key})...")
    engine = StrategyEngine()
    report = engine.analyze(
        repo=repo,
        tax_year=year,
        filing_status=fs,
        user_inputs=user_inputs,
    )
    conn.close()

    if json_output:
        typer.echo(json.dumps(report.model_dump(), cls=_DecimalEncoder, indent=2, default=str))
        return

    # Formatted output
    typer.echo("")
    typer.echo(f"=== Tax Strategy Analysis: {year} ({fs_key}) ===")
    typer.echo("")
    b = report.baseline_estimate
    typer.echo(f"Baseline Tax: ${b.total_tax:>12,.2f} "
               f"(Federal: ${b.federal_total_tax:,.2f} + California: ${b.ca_total_tax:,.2f})")
    typer.echo(f"Total Potential Savings: ${report.total_potential_savings:>12,.2f}")
    typer.echo("")

    recs = report.recommendations[:top_n]
    if not recs:
        typer.echo("No strategy recommendations generated.")
        typer.echo("Provide additional data (--prices, --age, --prior-year-tax) for more strategies.")
    else:
        typer.echo(f" {'#':>2} | {'Priority':<8} | {'Strategy':<40} | {'Savings':>10} | {'Deadline':<12}")
        typer.echo(f"{'---':>4}|{'-'*10}|{'-'*42}|{'-'*12}|{'-'*14}")
        for i, rec in enumerate(recs, 1):
            savings_str = f"${rec.estimated_savings:,.0f}" if rec.estimated_savings > 0 else "(info)"
            deadline_str = str(rec.deadline) if rec.deadline else "(ongoing)"
            typer.echo(
                f" {i:>2} | {rec.priority.value:<8} | {rec.name:<40} | {savings_str:>10} | {deadline_str:<12}"
            )

        typer.echo("")
        typer.echo("DETAILS:")
        for i, rec in enumerate(recs, 1):
            typer.echo("")
            typer.echo(f"[{i}] {rec.priority.value}: {rec.name}")
            typer.echo(f"    Situation: {rec.situation}")
            typer.echo(f"    Mechanism: {rec.mechanism}")
            typer.echo(f"    Impact: {rec.quantified_impact}")
            if rec.action_steps:
                typer.echo("    Action Steps:")
                for step in rec.action_steps:
                    typer.echo(f"      - {step}")
            if rec.california_impact:
                typer.echo(f"    CA Impact: {rec.california_impact}")
            if rec.irs_authority:
                typer.echo(f"    Authority: {rec.irs_authority}")

    if report.warnings:
        typer.echo("")
        typer.echo("WARNINGS:")
        for w in report.warnings:
            typer.echo(f"  - {w}")

    typer.echo("")
    typer.echo("DATA COMPLETENESS:")
    for key, available in report.data_completeness.items():
        marker = "x" if available else " "
        typer.echo(f"  [{marker}] {key.replace('_', ' ').title()}")


@app.command()
def report(
    year: int = typer.Argument(..., help="Tax year for report generation"),
    output: Path = typer.Option("reports/", help="Output directory for reports"),
    db: Path = typer.Option(
        Path.home() / ".taxbot" / "taxbot.db",
        "--db",
        help="Path to the SQLite database file",
    ),
    filing_status: str = typer.Option(
        "SINGLE",
        "--filing-status",
        "-s",
        help="Filing status: SINGLE, MFJ, MFS, HOH",
    ),
) -> None:
    """Generate all tax reports for a tax year."""
    from datetime import date as date_type

    from app.db.repository import TaxRepository
    from app.db.schema import create_schema
    from app.engines.estimator import TaxEstimator
    from app.engines.strategy import StrategyEngine, UserInputs
    from app.models.enums import (
        AdjustmentCode,
        FilingStatus,
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

    # Validate filing status
    status_map = {
        "SINGLE": "SINGLE",
        "MFJ": "MARRIED_FILING_JOINTLY",
        "MFS": "MARRIED_FILING_SEPARATELY",
        "HOH": "HEAD_OF_HOUSEHOLD",
    }
    fs_key = filing_status.upper()
    fs_value = status_map.get(fs_key, fs_key)
    try:
        fs = FilingStatus(fs_value)
    except ValueError:
        valid = ", ".join(status_map.keys())
        typer.echo(f"Error: Invalid filing status '{filing_status}'. Valid: {valid}", err=True)
        raise typer.Exit(1)

    if not db.exists():
        typer.echo("Error: No database found. Import data first with `taxbot import-data`.", err=True)
        raise typer.Exit(1)

    conn = create_schema(db)
    repo = TaxRepository(conn)

    # Create output directory
    output.mkdir(parents=True, exist_ok=True)

    typer.echo(f"Generating reports for year {year} to {output}...")
    typer.echo("")

    reports_generated = 0

    # Load sale results once (used by multiple reports)
    sale_result_rows = repo.get_sale_results(year)

    # Pre-build sale map for security lookups
    sale_rows = repo.get_sales(year)
    sale_map = {s["id"]: s for s in sale_rows}

    # Pre-build lot map for equity_type lookups
    lot_rows = repo.get_lots()
    lot_map = {lt["id"]: lt for lt in lot_rows}

    def _build_sale_result_fast(row: dict) -> SaleResult:
        """Reconstruct a SaleResult model using pre-loaded maps."""
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

    # --- 1. Form 8949 Report ---
    if sale_result_rows:
        try:
            sale_results = [_build_sale_result_fast(r) for r in sale_result_rows]
            gen = Form8949Generator()
            lines = gen.generate_lines(sale_results)
            content = gen.render(lines)
            path = output / f"{year}_form8949.txt"
            path.write_text(content)
            typer.echo(f"  [+] Form 8949:         {path}")
            reports_generated += 1
        except Exception as exc:
            typer.echo(f"  [!] Form 8949 failed: {exc}", err=True)
    else:
        typer.echo("  [-] Form 8949:         skipped (no sale results)")

    # --- 2. Reconciliation Report ---
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
                gain_loss_broker = (
                    (proceeds - broker_basis) if broker_basis is not None else None
                )
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
            typer.echo(f"  [+] Reconciliation:    {path}")
            reports_generated += 1
        except Exception as exc:
            typer.echo(f"  [!] Reconciliation failed: {exc}", err=True)
    else:
        typer.echo("  [-] Reconciliation:    skipped (no sale results)")

    # --- 3. ESPP Income Report ---
    espp_rows = [
        r for r in sale_result_rows
        if lot_map.get(r.get("lot_id"), {}).get("equity_type") == "ESPP"
    ]
    if espp_rows:
        try:
            from app.models.enums import DispositionType

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

                # Find associated event for offering/purchase details
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

                # Determine disposition type from holding period and dates
                def _add_years(d: date_type, years: int) -> date_type:
                    try:
                        return d.replace(year=d.year + years)
                    except ValueError:
                        return d.replace(year=d.year + years, day=28)

                two_years_from_offer = _add_years(offering_date, 2)
                one_year_from_purchase = _add_years(purchase_date, 1)
                if sale_date >= two_years_from_offer and sale_date >= one_year_from_purchase:
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
            typer.echo(f"  [+] ESPP Income:       {path}")
            reports_generated += 1
        except Exception as exc:
            typer.echo(f"  [!] ESPP Income failed: {exc}", err=True)
    else:
        typer.echo("  [-] ESPP Income:       skipped (no ESPP sales)")

    # --- 4. AMT Worksheet ---
    iso_events = repo.get_events(equity_type="ISO")
    iso_exercises = [
        ev for ev in iso_events if ev.get("event_type") == "EXERCISE"
    ]
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
            typer.echo(f"  [+] AMT Worksheet:     {path}")
            reports_generated += 1
        except Exception as exc:
            typer.echo(f"  [!] AMT Worksheet failed: {exc}", err=True)
    else:
        typer.echo("  [-] AMT Worksheet:     skipped (no ISO exercises)")

    # --- 5. Strategy Report ---
    try:
        engine = StrategyEngine()
        user_inputs = UserInputs()
        strategy_report = engine.analyze(
            repo=repo,
            tax_year=year,
            filing_status=fs,
            user_inputs=user_inputs,
        )
        if strategy_report.recommendations:
            gen_strategy = StrategyReportGenerator()
            content = gen_strategy.render(strategy_report.recommendations)
            path = output / f"{year}_strategy.txt"
            path.write_text(content)
            typer.echo(f"  [+] Strategy:          {path}")
            reports_generated += 1
        else:
            typer.echo("  [-] Strategy:          skipped (no recommendations)")
    except Exception as exc:
        typer.echo(f"  [!] Strategy failed: {exc}", err=True)

    # --- 6. Tax Estimate Summary ---
    try:
        estimator = TaxEstimator()
        estimate = estimator.estimate_from_db(
            repo=repo,
            tax_year=year,
            filing_status=fs,
        )
        gen_summary = TaxSummaryGenerator()
        content = gen_summary.render(estimate)
        path = output / f"{year}_tax_summary.txt"
        path.write_text(content)
        typer.echo(f"  [+] Tax Summary:       {path}")
        reports_generated += 1
    except Exception as exc:
        typer.echo(f"  [!] Tax Summary failed: {exc}", err=True)

    conn.close()

    typer.echo("")
    typer.echo(f"Done. {reports_generated} report(s) generated in {output}/")


@app.command()
def add_lot(
    equity_type: str = typer.Argument(..., help="Equity type: RSU, ISO, NSO, ESPP"),
    ticker: str = typer.Argument(..., help="Stock ticker symbol (e.g. COIN, AAPL)"),
    acquisition_date: str = typer.Argument(..., help="Date shares were acquired (YYYY-MM-DD)"),
    shares: int = typer.Argument(..., help="Number of shares"),
    cost_per_share: float = typer.Argument(..., help="Cost basis per share (e.g. IPO price, strike price)"),
    name: str = typer.Option(None, "--name", "-n", help="Company/security name"),
    amt_cost: float | None = typer.Option(None, "--amt-cost", help="AMT cost basis per share (ISOs only)"),
    event_type: str = typer.Option("VEST", "--event-type", help="Event type: VEST, EXERCISE, PURCHASE"),
    broker_source: str = typer.Option("MANUAL", "--broker", "-b", help="Broker source: MANUAL, SHAREWORKS, ROBINHOOD"),
    notes: str = typer.Option("", "--notes", help="Description or notes about this lot"),
    output: Path = typer.Option(
        Path("inputs/"),
        "--output",
        "-o",
        help="Output directory for the generated JSON file",
    ),
) -> None:
    """Create a manual equity lot JSON file for import.

    Use this when you have shares not covered by standard tax forms (e.g. pre-IPO
    RSUs, manual corrections, or lots missing from brokerage statements).

    Examples:

        taxbot add-lot RSU COIN 2020-02-20 181 250 --name "Coinbase" --notes "Pre-IPO RSU"

        taxbot add-lot ISO COIN 2021-04-19 250 18.71 --amt-cost 342.00 --event-type EXERCISE
    """
    from datetime import date as date_type

    from app.models.enums import BrokerSource as BS
    from app.models.enums import EquityType as ET
    from app.models.enums import TransactionType as TT

    # Validate equity type
    try:
        ET(equity_type.upper())
    except ValueError:
        valid = ", ".join(e.value for e in ET)
        typer.echo(f"Error: Invalid equity type '{equity_type}'. Valid: {valid}", err=True)
        raise typer.Exit(1)

    # Validate event type
    try:
        TT(event_type.upper())
    except ValueError:
        valid = ", ".join(t.value for t in TT)
        typer.echo(f"Error: Invalid event type '{event_type}'. Valid: {valid}", err=True)
        raise typer.Exit(1)

    # Validate broker source
    try:
        BS(broker_source.upper())
    except ValueError:
        valid = ", ".join(b.value for b in BS)
        typer.echo(f"Error: Invalid broker source '{broker_source}'. Valid: {valid}", err=True)
        raise typer.Exit(1)

    # Validate date
    try:
        acq_date = date_type.fromisoformat(acquisition_date)
    except ValueError:
        typer.echo(f"Error: Invalid date '{acquisition_date}'. Use YYYY-MM-DD format.", err=True)
        raise typer.Exit(1)

    if shares <= 0:
        typer.echo("Error: shares must be > 0", err=True)
        raise typer.Exit(1)
    if cost_per_share < 0:
        typer.echo("Error: cost_per_share must be >= 0", err=True)
        raise typer.Exit(1)

    record = {
        "tax_year": acq_date.year,
        "equity_type": equity_type.upper(),
        "ticker": ticker.upper(),
        "security_name": name or ticker.upper(),
        "acquisition_date": acquisition_date,
        "shares": shares,
        "cost_per_share": str(Decimal(str(cost_per_share))),
        "event_type": event_type.upper(),
        "broker_source": broker_source.upper(),
        "notes": notes,
    }
    if amt_cost is not None:
        record["amt_cost_per_share"] = str(Decimal(str(amt_cost)))

    # Write to JSON file (append if file exists with same ticker/year)
    output.mkdir(parents=True, exist_ok=True)
    base_name = f"equity_lots_{ticker.lower()}_{acq_date.year}"
    out_path = output / f"{base_name}.json"

    existing: list[dict] = []
    if out_path.exists():
        existing = json.loads(out_path.read_text())

    existing.append(record)
    out_path.write_text(json.dumps(existing, indent=2))

    typer.echo(f"Added {equity_type.upper()} lot: {shares} {ticker.upper()} shares at ${cost_per_share}/share ({acquisition_date})")
    typer.echo(f"Output: {out_path}")
    typer.echo(f"Import with: python -m app.cli import-data manual {out_path}")


class _DecimalEncoder(json.JSONEncoder):
    """JSON encoder that serializes Decimal as string."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


@app.command()
def parse(
    file: Path = typer.Argument(..., help="Path to the PDF tax form"),
    form_type: str | None = typer.Option(
        None,
        "--form-type",
        "-t",
        help="Form type: w2, 1099b, 1099div, 1099int, 3921, 3922 (auto-detected if omitted)",
    ),
    year: int | None = typer.Option(
        None,
        "--year",
        "-y",
        help="Tax year (auto-detected from form if omitted)",
    ),
    output: Path = typer.Option(
        Path("inputs/"),
        "--output",
        "-o",
        help="Output directory for the generated JSON file",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print extracted data to stdout without writing a file",
    ),
    vision: bool = typer.Option(
        False,
        "--vision",
        help="Use Claude Vision API for scanned/image-based PDFs (requires anthropic SDK)",
    ),
) -> None:
    """Parse a PDF tax form into JSON for import."""
    # Validate file exists and is PDF
    if not file.exists():
        typer.echo(f"Error: File not found: {file}", err=True)
        raise typer.Exit(1)
    if file.suffix.lower() != ".pdf":
        typer.echo(f"Error: File must be a PDF: {file}", err=True)
        raise typer.Exit(1)

    try:
        import pdfplumber
    except ImportError:
        typer.echo("Error: pdfplumber is not installed. Run: pip install pdfplumber", err=True)
        raise typer.Exit(1)

    from app.exceptions import ExtractionError, FormDetectionError, VisionExtractionError
    from app.parsing.detector import FormType, detect_form_type
    from app.parsing.extractors import get_extractor
    from app.parsing.redactor import Redactor

    # Extract text and tables from PDF (read-only, never copied)
    all_text = ""
    all_tables: list[list[list[str]]] = []
    try:
        with pdfplumber.open(file) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                all_text += page_text + "\n"
                page_tables = page.extract_tables() or []
                all_tables.extend(page_tables)
    except Exception as exc:
        typer.echo(f"Error: Could not read PDF: {exc}", err=True)
        raise typer.Exit(1)

    # Determine whether to use vision extraction
    use_vision = vision
    has_text = bool(all_text.strip())

    # Auto-fallback: if no text found, use vision if API key available
    if not has_text and not vision:
        import os
        if os.environ.get("ANTHROPIC_API_KEY"):
            typer.echo(
                "No text found in PDF (likely scanned/image-based). Using Claude Vision API...",
                err=True,
            )
            use_vision = True
        else:
            typer.echo(
                "Error: No text found in PDF (likely scanned/image-based). "
                "Set ANTHROPIC_API_KEY or use --vision to extract with Claude Vision API.",
                err=True,
            )
            raise typer.Exit(1)

    # --- Vision extraction path ---
    if use_vision:
        try:
            from app.parsing.vision import VisionExtractor
        except ImportError:
            typer.echo(
                "Error: anthropic package not installed. Run: pip install anthropic",
                err=True,
            )
            raise typer.Exit(1)

        typer.echo("Extracting with Claude Vision API...")
        try:
            extractor_v = VisionExtractor()
            images = extractor_v.pdf_to_images(file)
            typer.echo(f"  Converted {len(images)} page(s) to images")

            # Detect or validate form type via vision
            detected_type: FormType | None = None
            if form_type:
                try:
                    detected_type = FormType(form_type.lower())
                except ValueError:
                    valid = ", ".join(ft.value for ft in FormType)
                    typer.echo(f"Error: Unknown form type '{form_type}'. Valid types: {valid}", err=True)
                    raise typer.Exit(1)
            else:
                detected_type = extractor_v.detect_form_type(images)
                if detected_type is None:
                    raise FormDetectionError(str(file))

            typer.echo(f"Detected form type: {detected_type.value}")

            max_tokens = max(4096, len(images) * 2048)
            typer.echo(f"  Sending {len(images)} page(s) to Claude Vision (max_tokens={max_tokens})...")
            data = extractor_v.extract(images, detected_type)
        except VisionExtractionError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1)

        # Override tax year if specified
        if year:
            if isinstance(data, list):
                for record in data:
                    record["tax_year"] = year
            else:
                data["tax_year"] = year

        # Validate and warn using the regex extractor's logic (shared validation)
        regex_extractor = get_extractor(detected_type)
        errors = regex_extractor.validate_extraction(data)
        if errors:
            typer.echo("Extraction errors:", err=True)
            for error in errors:
                typer.echo(f"  - {error}", err=True)
            raise ExtractionError(str(file), errors)

        warnings = regex_extractor.get_warnings(data)
        if warnings:
            typer.echo("Plausibility warnings (review output for accuracy):", err=True)
            for warning in warnings:
                typer.echo(f"  ⚠ {warning}", err=True)

        # Scrub PII from output (layer 2 — prompt already instructed null for PII)
        redactor = Redactor()
        if isinstance(data, dict):
            data = redactor.scrub_output(data)
        elif isinstance(data, list):
            data = [redactor.scrub_output(record) for record in data]

        # Serialize and output
        json_output = json.dumps(data, indent=2, cls=_DecimalEncoder)

        if dry_run:
            typer.echo(json_output)
        else:
            output.mkdir(parents=True, exist_ok=True)
            file_year = year
            if not file_year:
                if isinstance(data, list) and data:
                    file_year = data[0].get("tax_year")
                elif isinstance(data, dict):
                    file_year = data.get("tax_year")
            file_year = file_year or "unknown"

            base_name = f"{detected_type.value}_{file_year}"
            out_path = output / f"{base_name}.json"
            counter = 2
            while out_path.exists():
                out_path = output / f"{base_name}_{counter}.json"
                counter += 1

            out_path.write_text(json_output)
            typer.echo(f"Output written to: {out_path}")

        record_count = len(data) if isinstance(data, list) else 1
        typer.echo(f"Extracted {record_count} record(s) from {detected_type.value} (vision)")
        return

    # --- Text/regex extraction path (digital PDF with text layer) ---
    # Redact PII from raw text
    redactor = Redactor()
    redaction_result = redactor.redact(all_text)
    redacted_text = redaction_result.text

    # Detect or validate form type
    detected_type = None
    if form_type:
        try:
            detected_type = FormType(form_type.lower())
        except ValueError:
            valid = ", ".join(ft.value for ft in FormType)
            typer.echo(f"Error: Unknown form type '{form_type}'. Valid types: {valid}", err=True)
            raise typer.Exit(1)
    else:
        detected_type = detect_form_type(redacted_text)
        if detected_type is None:
            raise FormDetectionError(str(file))

    typer.echo(f"Detected form type: {detected_type.value}")

    # Extract fields
    extractor = get_extractor(detected_type)
    data = extractor.extract(redacted_text, all_tables)

    # Override tax year if specified
    if year:
        if isinstance(data, list):
            for record in data:
                record["tax_year"] = year
        else:
            data["tax_year"] = year

    # Validate extraction (hard errors = missing required fields)
    errors = extractor.validate_extraction(data)
    if errors:
        typer.echo("Extraction errors:", err=True)
        for error in errors:
            typer.echo(f"  - {error}", err=True)
        raise ExtractionError(str(file), errors)

    # Plausibility warnings (non-blocking — output still generated)
    warnings = extractor.get_warnings(data)
    if warnings:
        typer.echo("Plausibility warnings (review output for accuracy):", err=True)
        for warning in warnings:
            typer.echo(f"  ⚠ {warning}", err=True)

    # Scrub PII from output
    if isinstance(data, dict):
        data = redactor.scrub_output(data)
    elif isinstance(data, list):
        data = [redactor.scrub_output(record) for record in data]

    # Serialize to JSON
    json_output = json.dumps(data, indent=2, cls=_DecimalEncoder)

    if dry_run:
        typer.echo(json_output)
    else:
        # Ensure output directory exists
        output.mkdir(parents=True, exist_ok=True)

        # Determine tax year for filename
        file_year = year
        if not file_year:
            if isinstance(data, list) and data:
                file_year = data[0].get("tax_year")
            elif isinstance(data, dict):
                file_year = data.get("tax_year")
        file_year = file_year or "unknown"

        # Generate output filename (avoid overwriting)
        base_name = f"{detected_type.value}_{file_year}"
        out_path = output / f"{base_name}.json"
        counter = 2
        while out_path.exists():
            out_path = output / f"{base_name}_{counter}.json"
            counter += 1

        out_path.write_text(json_output)
        typer.echo(f"Output written to: {out_path}")

    # Print summary
    record_count = len(data) if isinstance(data, list) else 1
    typer.echo(f"Extracted {record_count} record(s) from {detected_type.value}")
    if redaction_result.redactions_made:
        typer.echo("PII redacted:")
        for note in redaction_result.redactions_made:
            typer.echo(f"  - {note}")


if __name__ == "__main__":
    show_mascot()
    app()
