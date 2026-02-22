# TaxBot 9000 — CLAUDE.md

## Project Overview

TaxBot 9000 is a Python-based system that processes complex U.S. tax situations involving equity compensation (ESPP, RSUs, NSOs, ISOs). It is designed for a single California-resident W-2 employee who receives equity compensation from employers and trades through brokerages such as Morgan Stanley Shareworks and Robinhood.

### Purpose

1. **Tax Estimation** — Ingest W-2s, 1099-Bs, Forms 3921/3922, and brokerage statements to compute an accurate estimate of federal and California state tax liability.
2. **Tax Strategy Suggestions** — Analyze the taxpayer's current situation and recommend better filing strategies, cost-basis elections, and disposition timing.
3. **Future Tax Planning** — Propose forward-looking strategies (e.g., ISO exercise timing, ESPP holding periods, AMT credit utilization, tax-loss harvesting) to reduce future tax obligations.

### Inputs

| Form / Report | Description |
|---|---|
| W-2 | Wages, withholdings, equity compensation income (Boxes 12, 14) |
| 1099-B | Brokerage proceeds and cost-basis data |
| 1099-DIV | Dividend income |
| 1099-INT | Interest income |
| Form 3921 | ISO exercise records |
| Form 3922 | ESPP transfer records |
| Supplemental Statements | Shareworks/Robinhood lot-level detail and gain/loss reports |

### Outputs

| Output | Description |
|---|---|
| Tax Due Estimate | Federal + California state estimated tax liability |
| Form 8949 Draft | Cost-basis-corrected sales for IRS reporting |
| ESPP Income Report | Ordinary income and adjusted basis for ESPP dispositions |
| ISO AMT Worksheet | AMT preference items and credit carryforward tracking |
| Strategy Report | Actionable recommendations to reduce current and future taxes |
| Reconciliation Report | Audit trail comparing broker-reported vs. corrected values |

---

## Architecture

```
inputs/             — Raw tax documents (CSV, PDF extracts, manual entry)
app/
  ingestion/        — Import adapters (Shareworks, Robinhood, manual forms)
  normalization/    — Canonical ledger and event schema
  engines/          — Tax computation engines
    basis.py        — Cost-basis correction
    espp.py         — ESPP ordinary income and qualifying disposition logic
    iso_amt.py      — ISO AMT preference and credit tracking
    estimator.py    — Tax-due estimation (federal + CA)
    strategy.py     — Tax strategy analysis and recommendation engine
  models/           — Pydantic data models
  reports/          — Jinja2 report templates and export logic
  cli.py            — Typer CLI interface
resources/          — Reference materials (accounting textbooks, IRS publications)
tests/              — Pytest test suite
plans/              — Collaboration plans written by the CPA agent
```

---

## Technology Stack

- **Language:** Python 3.11+
- **Data:** Pandas, Pydantic v2
- **Storage:** SQLite (encrypted at rest)
- **CLI:** Typer
- **Templates:** Jinja2
- **Testing:** Pytest
- **Linting:** Ruff

---

## Tax Domain Rules

### RSUs (Restricted Stock Units)
- Ordinary income recognized at vest; included in W-2.
- Correct cost basis = FMV at vest date.
- Broker-reported basis is often $0 or incomplete — always correct it.

### NSOs (Non-Qualified Stock Options)
- Ordinary income recognized at exercise (spread between FMV and strike price).
- Correct cost basis = strike price + recognized ordinary income.

### ESPP (Employee Stock Purchase Plan)
- No taxable event at purchase.
- At sale, income depends on qualifying vs. disqualifying disposition:
  - **Qualifying:** Ordinary income = lesser of (discount at offering date, actual gain). Remainder is LTCG.
  - **Disqualifying:** Ordinary income = spread at purchase date. Remainder is short-term or long-term capital gain.
- Basis must be adjusted to prevent double taxation.

### ISOs (Incentive Stock Options)
- No regular income at exercise.
- Spread at exercise is an AMT preference item (Form 6251).
- Must track both regular and AMT cost basis.
- AMT credit carries forward and offsets regular tax in future years.

### Form 8949 Reconciliation
- Every sale must appear on Form 8949 with the correct cost basis.
- Adjustment codes (B, e, O) must be applied per IRS instructions.
- Proceeds must match 1099-B; basis corrections are shown as adjustments.

---

## Agent Collaboration Model

This project uses a multi-agent workflow orchestrated through shared plan files.

### Agents

| Agent | Role |
|---|---|
| **Tax Expert CPA** | Lead agent. Analyzes tax documents, writes plans into `plans/` folder, makes tax-law decisions. |
| **Accountant** | Implements accounting logic, validates calculations, ensures GAAP alignment. |
| **Tax Planner** | Designs strategies to minimize current and future tax liability. |
| **Python Engineer** | Implements code, builds engines, writes tests, maintains architecture. |

### Workflow

1. The **Tax Expert CPA** agent reads all inputs and writes a detailed plan to `plans/<task-name>.md`.
2. Other agents **read the plan** before starting any work.
3. Each agent appends their progress and decisions to the plan's log section.
4. The CPA agent reviews all work for tax-law correctness.

### Plan File Location

All collaboration plans live in the `plans/` directory. Every plan follows the template in `.claude/chat/template.md`.

---

## Authoritative References

All agents must reference these sources when making tax-law decisions:

- **IRS Publication 525** — Taxable and Nontaxable Income
- **IRS Publication 550** — Investment Income and Expenses
- **Form 8949 Instructions** — Sales and Other Dispositions of Capital Assets
- **Form 3921 Instructions** — Exercise of an Incentive Stock Option
- **Form 3922 Instructions** — Transfer of Stock Acquired Through an ESPP
- **Form 6251 Instructions** — Alternative Minimum Tax
- **California FTB Publication 1001** — Supplemental Guidelines to California Adjustments
- **California Schedule D / D-1** — Capital Gains and Losses
- **`resources/` folder** — Financial accounting textbook and supplemental materials

---

## Key Commands

```bash
# Run all tests
python -m pytest tests/ -v

# Lint
ruff check .

# CLI
python -m app.cli import inputs/ --year 2025
python -m app.cli reconcile --year 2025
python -m app.cli estimate --year 2025
python -m app.cli strategy --year 2025
python -m app.cli report --year 2025 --output reports/
```

---

## Development Conventions

- All monetary values stored as `Decimal` — never `float`.
- Dates stored as `datetime.date`.
- Every engine must produce a reconciliation log.
- Test coverage required for all tax computation paths.
- All IRS form references must cite the specific line number or box.
- Plans must be written before implementation begins.
- No secrets, SSNs, or real taxpayer data in source control.
