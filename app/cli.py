"""Typer CLI interface for TaxBot 9000."""

from pathlib import Path

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


if __name__ == "__main__":
    show_mascot()
    app()
