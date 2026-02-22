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


def _save_import_result(
    result: Any,
    source: str,
    file_path: Path,
    repo: Any,
) -> dict:
    """Save a parsed ImportResult to the database. Returns a summary dict."""
    from app.models.tax_forms import W2, Form1099DIV, Form1099INT

    # Duplicate warnings (non-blocking)
    for form in result.forms:
        if isinstance(form, W2) and repo.check_w2_duplicate(form.employer_name, form.tax_year):
            typer.echo(
                f"Warning: W-2 from {form.employer_name} ({form.tax_year}) already imported",
                err=True,
            )
    # Batch-level re-import protection
    if repo.check_batch_duplicate(str(file_path), result.tax_year):
        typer.echo(
            f"Warning: {file_path.name} already imported for tax year {result.tax_year}. Skipping.",
            err=True,
        )
        return {
            "source": source,
            "file": file_path.name,
            "form_type": result.form_type.value,
            "forms": 0,
            "events": 0,
            "lots": 0,
            "sales": 0,
            "skipped": True,
            "reason": "duplicate_batch",
        }

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
    skipped_events = 0
    for event in result.events:
        if repo.check_event_duplicate(
            event.event_type.value,
            event.event_date.isoformat(),
            str(event.shares),
        ):
            typer.echo(
                f"Warning: Duplicate event skipped: {event.event_type.value} "
                f"{event.event_date} ({event.shares} shares)",
                err=True,
            )
            skipped_events += 1
            continue
        repo.save_event(event, batch_id)
    skipped_lots = 0
    for lot in result.lots:
        if repo.check_lot_duplicate(
            lot.equity_type.value,
            lot.security.ticker,
            lot.acquisition_date.isoformat(),
            str(lot.shares),
        ):
            typer.echo(
                f"Warning: Duplicate lot skipped: {lot.equity_type.value} "
                f"{lot.acquisition_date} ({lot.shares} shares)",
                err=True,
            )
            skipped_lots += 1
            continue
        repo.save_lot(lot, batch_id)

    skipped_sales = 0
    for sale in result.sales:
        if repo.check_sale_duplicate(
            sale.security.ticker,
            sale.sale_date.isoformat(),
            str(sale.shares),
            str(sale.proceeds_per_share),
        ):
            typer.echo(
                f"Warning: Duplicate sale skipped: {sale.security.ticker} "
                f"{sale.sale_date} ({sale.shares} shares)",
                err=True,
            )
            skipped_sales += 1
            continue
        repo.save_sale(sale, batch_id)

    return {
        "source": source,
        "file": file_path.name,
        "form_type": result.form_type.value,
        "forms": len(result.forms),
        "events": len(result.events),
        "lots": len(result.lots),
        "sales": len(result.sales) - skipped_sales,
        "skipped_sales": skipped_sales,
    }


def _process_pdf(file_path: Path, year: int, repo: Any) -> dict:
    """Parse a PDF tax form and import the result. Returns a summary dict."""
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

    # 2. Vision fallback if no text and API key available
    used_vision = False
    if not all_text.strip() and os.environ.get("ANTHROPIC_API_KEY"):
        from app.parsing.vision import VisionExtractor

        typer.echo(f"  {file_path.name}: No text found, using Vision API...", err=True)
        extractor_v = VisionExtractor()
        images = extractor_v.pdf_to_images(file_path)
        detected_type = extractor_v.detect_form_type(images)
        if detected_type is None:
            raise ValueError(f"Could not detect form type in {file_path.name}")
        data = extractor_v.extract(images, detected_type)
        used_vision = True
    elif not all_text.strip():
        raise ValueError(
            f"No text found in {file_path.name} (scanned PDF). "
            "Set ANTHROPIC_API_KEY to use Vision extraction."
        )
    else:
        detected_type = None
        data = None

    # 3. Text-based extraction path
    if not used_vision:
        redactor = Redactor()
        redaction_result = redactor.redact(all_text)
        redacted_text = redaction_result.text

        detected_type = detect_form_type(redacted_text)

        # Fallback to Vision if text detection fails
        if detected_type is None:
            if os.environ.get("ANTHROPIC_API_KEY"):
                from app.parsing.vision import VisionExtractor

                extractor_v = VisionExtractor()
                images = extractor_v.pdf_to_images(file_path)
                detected_type = extractor_v.detect_form_type(images)
                used_vision = True
            if detected_type is None:
                raise ValueError(f"Could not detect form type in {file_path.name}")

        if used_vision:
            data = extractor_v.extract(images, detected_type)
        else:
            extractor = get_extractor(detected_type)
            data = extractor.extract(redacted_text, all_tables)

            # Fallback to Vision if regex extraction returned empty
            if (isinstance(data, list) and len(data) == 0) or (isinstance(data, dict) and not data):
                if os.environ.get("ANTHROPIC_API_KEY"):
                    from app.parsing.vision import VisionExtractor

                    extractor_v = VisionExtractor()
                    images = extractor_v.pdf_to_images(file_path)
                    data = extractor_v.extract(images, detected_type)
                    used_vision = True

    # 4. Validate extraction (fall back to Vision if validation fails)
    regex_extractor = get_extractor(detected_type)
    errors = regex_extractor.validate_extraction(data)
    if errors and not used_vision and os.environ.get("ANTHROPIC_API_KEY"):
        from app.parsing.vision import VisionExtractor

        typer.echo(
            f"  {file_path.name}: Regex extraction incomplete, falling back to Vision API...",
            err=True,
        )
        extractor_v = VisionExtractor()
        images = extractor_v.pdf_to_images(file_path)
        data = extractor_v.extract(images, detected_type)
        used_vision = True
        errors = regex_extractor.validate_extraction(data)

    if errors:
        raise ValueError(
            f"Extraction errors in {file_path.name}: " + "; ".join(errors)
        )

    # 5. Scrub PII from output
    if not used_vision:
        redactor = Redactor()
    else:
        from app.parsing.redactor import Redactor
        redactor = Redactor()
    if isinstance(data, dict):
        data = redactor.scrub_output(data)
    elif isinstance(data, list):
        data = [redactor.scrub_output(record) for record in data]

    # 6. Set tax year on all records
    if isinstance(data, list):
        for record in data:
            record["tax_year"] = year
    else:
        data["tax_year"] = year

    # 7. Write temp JSON → import via ManualAdapter
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False,
    ) as tmp:
        json.dump(data, tmp, default=str)
        tmp_path = Path(tmp.name)

    try:
        from app.ingestion.manual import ManualAdapter

        adapter = ManualAdapter()
        result = adapter.parse(tmp_path)
        result.tax_year = year
        for form in result.forms:
            form.tax_year = year
        val_errors = adapter.validate(result)
        if val_errors:
            raise ValueError(
                f"Validation errors in {file_path.name}: " + "; ".join(val_errors)
            )
        summary = _save_import_result(result, "pdf", file_path, repo)
        summary["form_type"] = detected_type.value
        return summary
    finally:
        tmp_path.unlink(missing_ok=True)


def _process_csv(file_path: Path, year: int, repo: Any) -> dict:
    """Parse a CSV file via RobinhoodAdapter and import. Returns a summary dict."""
    from app.ingestion.robinhood import RobinhoodAdapter

    adapter = RobinhoodAdapter()
    result = adapter.parse(file_path)
    result.tax_year = year
    for form in result.forms:
        form.tax_year = year
    errors = adapter.validate(result)
    if errors:
        raise ValueError(
            f"Validation errors in {file_path.name}: " + "; ".join(errors)
        )
    return _save_import_result(result, "robinhood", file_path, repo)


def _process_json(file_path: Path, year: int, repo: Any) -> dict:
    """Parse a JSON file via ManualAdapter and import. Returns a summary dict."""
    from app.ingestion.manual import ManualAdapter

    adapter = ManualAdapter()
    result = adapter.parse(file_path)
    result.tax_year = year
    for form in result.forms:
        form.tax_year = year
    errors = adapter.validate(result)
    if errors:
        raise ValueError(
            f"Validation errors in {file_path.name}: " + "; ".join(errors)
        )
    return _save_import_result(result, "manual", file_path, repo)


def _print_import_summary(results: list[dict], errors: list[tuple[str, str]], db_path: Path, year: int) -> None:
    """Print the import summary table."""
    typer.echo("")
    typer.echo("=== Import Summary ===")
    typer.echo("")
    header = (
        f"  {'#':>3} | {'File':<30} | {'Type':<8}"
        f" | {'Forms':>5} | {'Events':>6} | {'Lots':>4} | {'Sales':>5} | Status"
    )
    typer.echo(header)
    sep = f" {'-'*4}|{'-'*32}|{'-'*10}|{'-'*7}|{'-'*8}|{'-'*6}|{'-'*7}|{'-'*8}"
    typer.echo(sep)

    idx = 0
    all_entries = []

    for r in results:
        idx += 1
        all_entries.append((
            idx, r["file"], r.get("form_type", "--"),
            r["forms"], r["events"], r["lots"], r["sales"], "OK", "",
        ))

    for fname, reason in errors:
        idx += 1
        all_entries.append((idx, fname, "--", "--", "--", "--", "--", "ERROR", reason))

    for entry in sorted(all_entries, key=lambda x: x[0]):
        i, fname, ftype, forms, events, lots, sales, status = entry[:8]
        line = (
            f"  {i:>3} | {fname:<30} | {ftype:<8}"
            f" | {str(forms):>5} | {str(events):>6}"
            f" | {str(lots):>4} | {str(sales):>5} | {status}"
        )
        typer.echo(line)
        if status == "ERROR" and len(entry) > 8 and entry[8]:
            typer.echo(f"        Reason: {entry[8]}")

    total = len(results) + len(errors)
    typer.echo("")
    typer.echo(f"Imported {len(results)} of {total} file(s) into {db_path.name} (tax year {year})")


@app.command(name="import")
def import_cmd(
    directory: Path = typer.Argument(..., help="Directory containing tax documents (.pdf, .csv, .json)"),
    year: int = typer.Option(
        ...,
        "--year",
        "-y",
        help="Tax year for import",
    ),
    db: Path = typer.Option(
        Path.home() / ".taxbot" / "taxbot.db",
        "--db",
        help="Path to the SQLite database file",
    ),
) -> None:
    """Import tax documents from a directory into the TaxBot database.

    Scans the directory for .pdf, .csv, and .json files, auto-detects
    file types, parses them, and imports into the database.

    For a complete tax estimation, provide the following documents:

    \b
    Required:
      W-2                     Wages, withholdings, equity comp (from each employer)
      1099-B                  Brokerage proceeds and cost basis (Shareworks, Robinhood)
    \b
    Equity Compensation:
      Form 3921               ISO exercise records
      Form 3922               ESPP transfer/purchase records
      RSU Releases Report     Shareworks "Releases Report (Details)" with vest
                              dates, FMV, and share counts (replaces auto-created lots)
    \b
    Other Income:
      1099-DIV                Dividend income
      1099-INT                Interest income
    Supported formats: PDF (with Vision API fallback for scans), CSV, JSON.
    Set ANTHROPIC_API_KEY for scanned PDF support.
    """
    if not directory.exists() or not directory.is_dir():
        typer.echo(f"Error: Directory not found: {directory}", err=True)
        raise typer.Exit(1)

    # Collect files sorted by name
    supported_exts = {".pdf", ".csv", ".json"}
    files = sorted(
        [f for f in directory.iterdir() if f.is_file() and f.suffix.lower() in supported_exts],
        key=lambda f: f.name,
    )

    if not files:
        typer.echo(f"No .pdf, .csv, or .json files found in {directory}")
        raise typer.Exit(0)

    from app.db.repository import TaxRepository
    from app.db.schema import create_schema

    # Initialize database
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = create_schema(db)
    repo = TaxRepository(conn)

    results: list[dict] = []
    errors: list[tuple[str, str]] = []

    for file_path in files:
        ext = file_path.suffix.lower()
        try:
            if ext == ".pdf":
                summary = _process_pdf(file_path, year, repo)
            elif ext == ".csv":
                summary = _process_csv(file_path, year, repo)
            elif ext == ".json":
                summary = _process_json(file_path, year, repo)
            else:
                continue
            results.append(summary)
        except Exception as exc:
            typer.echo(f"Error processing {file_path.name}: {exc}", err=True)
            errors.append((file_path.name, str(exc)))

    conn.close()

    _print_import_summary(results, errors, db, year)

    if errors:
        raise typer.Exit(1)


def _print_data_gaps(run: dict) -> None:
    """Render the structured data gap analysis from a reconciliation run."""
    from app.models.data_gaps import DataGapReport, GapSeverity

    gap_report: DataGapReport | None = run.get("data_gap_report")
    if gap_report is None:
        return

    typer.echo("")
    typer.echo("=== Data Gap Analysis ===")
    typer.echo("")

    if not gap_report.gaps:
        typer.echo("No data gaps detected. All sales matched to imported lots.")
        return

    severity_icons = {
        GapSeverity.ERROR: "[!!]",
        GapSeverity.WARNING: "[!]",
        GapSeverity.INFO: "[i]",
    }

    for gap in gap_report.gaps:
        icon = severity_icons.get(gap.severity, "[?]")
        typer.echo(f"  {icon} {gap.ticker}: {gap.summary}")

        if gap.date_range_start and gap.date_range_end:
            typer.echo(
                f"      Vest dates: {gap.date_range_start.isoformat()} "
                f"to {gap.date_range_end.isoformat()}"
            )

        if gap.missing_document:
            typer.echo(f"      Missing: {gap.missing_document}")

        if gap.suggested_action:
            typer.echo(f"      -> {gap.suggested_action}")

        typer.echo("")

    if gap_report.has_blocking_gaps:
        typer.echo(
            "  Some gaps have ERROR severity and may affect tax accuracy."
        )


@app.command()
def reconcile(
    year: int = typer.Argument(..., help="Tax year to reconcile"),
    db: Path = typer.Option(
        Path.home() / ".taxbot" / "taxbot.db",
        "--db",
        help="Path to the SQLite database file",
    ),
    no_prompt: bool = typer.Option(
        False,
        "--no-prompt",
        "--batch",
        help="Skip interactive prompts (batch mode)",
    ),
) -> None:
    """Run basis correction and reconciliation for a tax year."""
    import sys

    from app.db.repository import TaxRepository
    from app.db.schema import create_schema
    from app.engines.reconciliation import ReconciliationEngine

    if not db.exists():
        typer.echo("Error: No database found. Import data first with `taxbot import`.", err=True)
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

    # Data gap analysis (replaces raw warnings for auto-created lots)
    _print_data_gaps(run)

    # Still show non-auto-created warnings
    other_warnings = [
        w for w in (run.get("warnings") or [])
        if "Auto-created RSU lot for " not in w
        and "date_acquired is 'Various'" not in w
    ]
    if other_warnings:
        typer.echo("\nWarnings:")
        for w in other_warnings:
            typer.echo(f"  - {w}")

    if run.get("errors"):
        typer.echo("\nErrors:")
        for e in run["errors"]:
            typer.echo(f"  - {e}")

    typer.echo(f"\nStatus: {run['status']}")

    # Interactive prompt when gaps exist and not in batch mode
    gap_report = run.get("data_gap_report")
    if (
        gap_report
        and gap_report.gaps
        and not no_prompt
        and sys.stdin.isatty()
    ):
        typer.echo("")
        typer.echo("Options:")
        typer.echo("  [1] Continue anyway")
        typer.echo("  [2] Show detailed lot list")
        typer.echo("  [3] Exit to import missing documents")
        choice = typer.prompt("Select", default="1")
        if choice == "2":
            # Show the raw auto-created lot warnings
            auto_warnings = [
                w for w in (run.get("warnings") or [])
                if "Auto-created RSU lot for " in w
            ]
            if auto_warnings:
                typer.echo("\nDetailed auto-created lots:")
                for w in auto_warnings:
                    typer.echo(f"  - {w}")
            else:
                typer.echo("\nNo detailed lot data to display.")
        elif choice == "3":
            typer.echo("\nExiting. Import missing documents and re-run reconciliation.")
            raise typer.Exit(0)


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
        typer.echo("Error: No database found. Import data first with `taxbot import`.", err=True)
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
        typer.echo("Error: No database found. Import data first with `taxbot import`.", err=True)
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
        typer.echo("Error: No database found. Import data first with `taxbot import`.", err=True)
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


class _DecimalEncoder(json.JSONEncoder):
    """JSON encoder that serializes Decimal as string."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


@app.command()
def chat(
    db: Path = typer.Option(
        Path.home() / ".taxbot" / "taxbot.db",
        "--db",
        help="Path to the SQLite database file",
    ),
    year: int = typer.Option(2024, "--year", "-y", help="Tax year for context"),
    model: str = typer.Option(
        "claude-sonnet-4-20250514",
        "--model",
        "-m",
        help="Claude model to use",
    ),
) -> None:
    """Interactive CPA expert chat for tax questions."""
    import os

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        typer.echo(
            "Error: ANTHROPIC_API_KEY environment variable is not set.\n"
            "Set it with: export ANTHROPIC_API_KEY=your-key-here",
            err=True,
        )
        raise typer.Exit(1)

    try:
        import anthropic
    except ImportError:
        typer.echo(
            "Error: anthropic package not installed. Run: pip install anthropic",
            err=True,
        )
        raise typer.Exit(1)

    try:
        from rich.console import Console
    except ImportError:
        typer.echo(
            "Error: rich package not installed. Run: pip install rich",
            err=True,
        )
        raise typer.Exit(1)

    from app.chat import build_system_prompt, run_chat
    from app.db.repository import TaxRepository
    from app.db.schema import create_schema

    # Initialize database (creates if needed)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = create_schema(db)
    repo = TaxRepository(conn)

    system_prompt = build_system_prompt(repo, year)
    conn.close()

    client = anthropic.Anthropic(api_key=api_key)
    console = Console()

    run_chat(console, client, model, system_prompt)


if __name__ == "__main__":
    show_mascot()
    app()
