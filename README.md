> THIS PROJECT IS ONLY FOR TESTING - NEVER USE IT FOR REAL DATA AND NEVER TRUST THE RESULTS.

# TaxBot 9000
```
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

A Python CLI tool that processes U.S. tax documents for equity compensation (RSUs, ESPP, ISOs, NSOs). It ingests W-2s, 1099-Bs, Forms 3921/3922, and brokerage statements, then corrects cost basis, computes ESPP ordinary income, and estimates federal + California tax liability.

## Architecture

```
                  ┌─────────────────────────────────┐
                  │          CLI (Typer)             │
                  │  import · reconcile · estimate   │
                  └──────────┬──────────────────────┘
                             │
              ┌──────────────┼──────────────────┐
              ▼              ▼                  ▼
      ┌──────────────┐ ┌───────────┐   ┌──────────────┐
      │   Parsing     │ │  Engines  │   │   Reports    │
      │              │ │           │   │              │
      │ PDF/CSV/JSON │ │ Basis     │   │ Form 8949    │
      │ detection &  │ │ ESPP      │   │ ESPP Income  │
      │ extraction   │ │ ISO/AMT   │   │ Reconcile    │
      │              │ │ Estimator │   │              │
      └──────┬───────┘ └─────┬─────┘   └──────────────┘
             │               │
             ▼               ▼
      ┌────────────────────────────┐
      │     SQLite Database        │
      │  sales · lots · events     │
      │  w2s · import_batches      │
      └────────────────────────────┘
```

**Import** parses PDFs (via Vision API), CSVs, and JSON files, auto-detects the form type (W-2, 1099-B, 3922, etc.), and stores normalized records in SQLite. Duplicate detection prevents the same sales, events, and lots from being imported twice.

**Reconcile** matches each sale to its purchase lot (FIFO), corrects the cost basis, computes ESPP ordinary income (qualifying vs. disqualifying), and generates Form 8949 adjustment codes.

**Estimate** pulls reconciled data and W-2 info to compute federal and California tax liability with progressive brackets, NIIT, AMT, and mental health surcharge.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

```bash
# 1. Import tax documents (scans directory for PDFs, CSVs, JSON files)
python -m app.cli import ~/Desktop/tax_2024/ --year 2024

# 2. Reconcile sales against lots and correct cost basis
python -m app.cli reconcile 2024

# 3. Estimate tax liability
python -m app.cli estimate 2024
```

To start fresh, delete the database and re-import:

```bash
rm ~/.taxbot/taxbot.db
python -m app.cli import ~/Desktop/tax_2024/ --year 2024
python -m app.cli reconcile 2024
python -m app.cli estimate 2024
```

### Tips

- Only put annual tax forms (W-2, 1099-B, 3922) in the import directory. Quarterly statements create duplicate aggregate sales.
- Form 3922 data can also be provided as JSON in a separate directory (see `inputs/` for the schema).
- The import command deduplicates sales, events, and lots automatically, so re-importing the same file is safe.

## Running Tests

```bash
python -m pytest tests/ -v
```

## License

Private. Not for distribution.
