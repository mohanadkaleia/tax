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
    source: str = typer.Argument(..., help="Data source: manual (shareworks, robinhood coming soon)"),
    file: Path = typer.Argument(..., help="Path to the JSON file produced by `taxbot parse`"),
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
    if file.suffix.lower() != ".json":
        typer.echo(f"Error: File must be JSON: {file}", err=True)
        raise typer.Exit(1)

    # Validate source
    valid_sources = {"manual", "shareworks", "robinhood"}
    if source.lower() not in valid_sources:
        typer.echo(
            f"Error: Unknown source '{source}'. Valid sources: {', '.join(sorted(valid_sources))}",
            err=True,
        )
        raise typer.Exit(1)

    if source.lower() != "manual":
        typer.echo(f"Error: '{source}' adapter not yet implemented. Use 'manual'.", err=True)
        raise typer.Exit(1)

    from app.db.repository import TaxRepository
    from app.db.schema import create_schema
    from app.ingestion.manual import ManualAdapter
    from app.models.tax_forms import W2, Form1099DIV, Form1099INT

    # Parse JSON through ManualAdapter
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
) -> None:
    """Run basis correction and reconciliation for a tax year."""
    typer.echo(f"Reconciling tax year {year}...")
    typer.echo("Reconciliation not yet implemented.")


@app.command()
def estimate(
    year: int = typer.Argument(..., help="Tax year to estimate"),
) -> None:
    """Compute estimated tax liability for a tax year."""
    typer.echo(f"Estimating tax for year {year}...")
    typer.echo("Estimation not yet implemented.")


@app.command()
def strategy(
    year: int = typer.Argument(..., help="Tax year for strategy analysis"),
) -> None:
    """Run tax strategy analysis and recommendations."""
    typer.echo(f"Analyzing tax strategies for year {year}...")
    typer.echo("Strategy analysis not yet implemented.")


@app.command()
def report(
    year: int = typer.Argument(..., help="Tax year for report generation"),
    output: Path = typer.Option("reports/", help="Output directory for reports"),
) -> None:
    """Generate all tax reports for a tax year."""
    typer.echo(f"Generating reports for year {year} to {output}...")
    typer.echo("Report generation not yet implemented.")


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
