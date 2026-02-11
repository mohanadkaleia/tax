
# EquityTax Reconciler – Technical Design Document

**Author Perspective:** Senior architect engineer with deep experience in U.S. taxation, equity compensation, and accounting systems.  
**Scope:** Single user, California resident, W-2 employee with ESPP, RSUs, ISOs, and NSOs.  
**Data Sources:** Morgan Stanley Shareworks and Robinhood.

---

## 0) Goals and Non‑Goals

### Goals
1. Provide a reliable system for a single California full‑year resident.
2. Support structured inputs:
   - W‑2 data
   - Morgan Stanley Shareworks 1099‑B and supplemental information
   - Robinhood 1099 data
   - IRS Forms 3921 and 3922
3. Produce outputs that simplify entry into tax software:
   - Form 8949 with correct cost basis
   - ESPP ordinary income calculations
   - ISO AMT worksheets
   - Detailed audit trail reports

### Non‑Goals
- Full tax return filing
- Multi‑state residency or part‑year allocations
- Business income, K‑1s, rentals, or foreign reporting
- Real‑time brokerage integrations

---

## 1) Tax Domain Rules

### RSUs
- Income is recognized at vest and included in W‑2 wages.
- Correct basis equals FMV at vest.
- Broker‑reported basis is often incomplete and requires correction.

### NSOs
- Ordinary income recognized at exercise.
- Basis equals strike price plus recognized income.

### ESPP
- No income at purchase.
- Income depends on qualifying vs disqualifying disposition.
- Basis must be adjusted to avoid double taxation.

### ISOs
- No regular income at exercise.
- May trigger AMT adjustment.
- Requires tracking of AMT basis and credits.

### Form 8949
- Required for reconciling broker data.
- Adjustments must be reported with appropriate IRS codes.

---

## 2) System Architecture

### Components

1. **Ingestion Layer**
   - Import CSV/PDF‑derived data
   - Normalize into structured events

2. **Normalization Layer**
   - Convert transactions into canonical ledger

3. **Tax Engines**
   - Basis correction engine
   - ESPP income engine
   - ISO AMT engine

4. **Reporting Layer**
   - 8949 export
   - Human‑readable reconciliation reports

5. **CLI Interface**
   - Commands for import, reconciliation, and export

---

## 3) Data Model

### Core Entities

- Security
- Account
- EquityEvent
- Lot
- SaleAllocation

### Raw Import Tables

- import_batch
- raw_rows

---

## 4) Ingestion Adapters

### Shareworks Adapter
- Parse 1099‑B exports
- Extract supplemental data
- Convert to normalized events

### Robinhood Adapter
- Import consolidated 1099 data
- Normalize to common schema

---

## 5) Computation Engines

### Lot Matching
- Build acquisition lots
- Match sales to lots using FIFO or broker identifiers
- Maintain audit log

### Basis Correction
- Compute correct cost basis
- Generate 8949 adjustments

### ESPP Logic
- Determine qualifying status
- Compute ordinary income

### ISO AMT
- Compute AMT adjustments
- Track AMT credit carryforwards

---

## 6) Reconciliation Checks

- Validate W‑2 consistency
- Compare broker vs computed values
- Ensure lot integrity
- Guarantee reproducibility

---

## 7) Deliverables

### Milestones

1. Project Skeleton
2. Shareworks Import
3. Robinhood Import
4. Manual W‑2 / 3921 / 3922 Support
5. Lot Builder
6. 8949 Engine
7. ESPP Engine
8. ISO AMT Engine
9. Final Reporting Pack

---

## 8) Implementation Stack

- Python
- Pandas
- Pydantic
- SQLite
- Typer CLI
- Jinja2 templates

---

## 9) Security

- Local‑only storage
- Encrypted database
- No sensitive logging

---

## 10) Authoritative References

- IRS Publication 525
- Form 8949 Instructions
- Form 3921 / 3922 Instructions
- Form 6251 (AMT)
- California FTB AMT Guidance

---

## 11) Next Steps

1. Implement imports
2. Add RSU basis correction
3. Add ESPP engine
4. Add ISO AMT tracking

---

**End of Design Document**
