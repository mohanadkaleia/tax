> THIS PROJECT IS ONLY FOR TESTING - NEVER USE IT FOR REAL DATA AND NEVER TRUST THE RESULTS.

# TaxBot 9000
```bash
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
```

A Python-based tax reconciliation system for U.S. equity compensation. It processes W-2s, 1099-Bs, Forms 3921/3922, and brokerage statements from Morgan Stanley Shareworks and Robinhood to produce corrected cost-basis reports, tax estimates, and filing-ready Form 8949 output.

Designed for a single California-resident W-2 employee with RSUs, ISOs, NSOs, and ESPP shares.

## What It Does

- **Cost-basis correction** — Fixes the broker-reported basis that is often $0 or incomplete for equity compensation sales.
- **Form 8949 generation** — Produces IRS-ready sales schedules with proper adjustment codes (B, e, O).
- **ESPP income computation** — Determines qualifying vs. disqualifying dispositions and computes ordinary income to prevent double taxation.
- **ISO AMT tracking** — Calculates AMT preference items and tracks credit carryforwards across tax years.
- **Tax estimation** — Computes federal and California state tax liability with progressive brackets, NIIT, and AMT.
- **Strategy recommendations** — Analyzes tax-loss harvesting, ESPP holding period optimization, ISO exercise timing, and more.
- **Reconciliation reports** — Audit trail comparing broker-reported values against corrected values for every transaction.

## Supported Inputs

| Form / Source | Description |
|---|---|
| W-2 | Wages, withholdings, equity comp income (Boxes 12, 14) |
| 1099-B | Brokerage proceeds and cost-basis data |
| 1099-DIV | Dividend income |
| 1099-INT | Interest income |
| Form 3921 | ISO exercise records |
| Form 3922 | ESPP transfer records |
| Shareworks | Morgan Stanley supplemental lot-level detail |
| Robinhood | Consolidated 1099 data |

## Requirements

- Python 3.11+

## Setup

```bash
# Clone the repository
git clone <repo-url>
cd tax

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install the package with dev dependencies
pip install -e ".[dev]"
```

## Usage

All commands are available through the CLI:

```bash
# Show available commands
python -m app.cli --help

# Import data from a brokerage source
python -m app.cli import-data shareworks inputs/1099b.csv --year 2025
python -m app.cli import-data robinhood inputs/robinhood_1099.csv --year 2025
python -m app.cli import-data manual inputs/w2.json --year 2025

# Run cost-basis reconciliation
python -m app.cli reconcile 2025

# Compute estimated tax liability
python -m app.cli estimate 2025

# Run tax strategy analysis
python -m app.cli strategy 2025

# Generate all reports to an output directory
python -m app.cli report 2025 --output reports/
```

## Project Structure

```
app/
  cli.py                  # Typer CLI entry point
  exceptions.py           # Typed error hierarchy
  ingestion/              # Import adapters
    base.py               #   Abstract adapter interface
    shareworks.py          #   Morgan Stanley Shareworks
    robinhood.py           #   Robinhood
    manual.py              #   Manual entry (W-2, 3921, 3922)
  normalization/          # Canonical ledger and event processing
    events.py              #   Event deduplication and validation
    ledger.py              #   Lot builder and sale matching
  engines/                # Tax computation engines
    basis.py               #   Cost-basis correction (RSU, NSO, ESPP, ISO)
    espp.py                #   ESPP qualifying/disqualifying disposition logic
    iso_amt.py             #   ISO AMT preference and credit tracking
    estimator.py           #   Federal + California tax estimation
    strategy.py            #   Tax strategy recommendations
    lot_matcher.py         #   FIFO and specific-ID lot matching
    brackets.py            #   Tax bracket configuration (federal + CA)
  models/                 # Pydantic data models
    enums.py               #   EquityType, FilingStatus, Form8949Category, etc.
    equity_event.py        #   Security, Lot, EquityEvent, Sale, SaleResult
    tax_forms.py           #   W2, Form1099B, Form3921, Form3922, etc.
    reports.py             #   Form8949Line, TaxEstimate, AuditEntry, etc.
  db/                     # SQLite persistence
    schema.py              #   Database schema definition
    repository.py          #   Data access layer
    migrations.py          #   Schema versioning
  reports/                # Report generators
    form8949.py            #   Form 8949 output
    espp_report.py         #   ESPP income report
    amt_worksheet.py       #   ISO AMT worksheet
    reconciliation.py      #   Broker vs. corrected basis comparison
    strategy_report.py     #   Strategy recommendations
    templates/             #   Jinja2 report templates
inputs/                   # Raw tax documents (not committed)
plans/                    # Agent collaboration plans
resources/                # IRS publications and reference materials
tests/                    # Pytest test suite
```

## Running Tests

```bash
# Run all tests
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_engines/test_basis.py -v

# Run with short summary
python -m pytest tests/ --tb=short
```

## Linting

```bash
ruff check app/ tests/
```

## Key Design Decisions

- **Decimal everywhere** — All monetary values use Python's `Decimal` type, never `float`, to avoid rounding errors on tax forms.
- **Dual-basis tracking** — ISOs maintain both regular tax basis and AMT basis from day one.
- **Immutable audit trail** — Every computation step is logged for traceability and review.
- **Configurable brackets** — Tax brackets are stored as data structures keyed by year and filing status, not hardcoded in formulas.
- **Broker data is never trusted** — The entire system is built around the assumption that broker-reported cost basis is wrong and must be corrected.

## Tax Domain Overview

| Equity Type | Income Event | Basis Rule | Key Form |
|---|---|---|---|
| RSU | Ordinary income at vest (W-2) | FMV at vest date | 1099-B |
| NSO | Ordinary income at exercise (W-2 Box 12 Code V) | Strike + recognized income | 1099-B |
| ESPP | Ordinary income at sale (qualifying or disqualifying) | Purchase price + ordinary income | 3922, 1099-B |
| ISO | No regular income at exercise; AMT preference | Strike (regular) / FMV at exercise (AMT) | 3921, 6251 |

## Development Status

The project skeleton is complete with working engines for:
- RSU and NSO cost-basis correction
- ESPP qualifying/disqualifying disposition computation
- ISO AMT preference calculation
- Federal and California tax estimation
- FIFO lot matching

Ingestion adapters (Shareworks, Robinhood, manual) are stubbed and ready for implementation.

## Security

- All data is stored locally. No network calls, no cloud services.
- SQLite database uses WAL mode.
- No SSNs, real taxpayer data, or secrets are committed to source control.
- Input files in `inputs/` are gitignored by default.

## License

Private. Not for distribution.
