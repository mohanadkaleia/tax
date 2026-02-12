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
    source: str = typer.Argument(..., help="Data source: shareworks, robinhood, manual"),
    file: Path = typer.Argument(..., help="Path to the input file"),
    year: int = typer.Option(..., help="Tax year"),
) -> None:
    """Import tax data from a brokerage or manual source."""
    typer.echo(f"Importing from {source}: {file} for tax year {year}")
    typer.echo("Import not yet implemented.")


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
