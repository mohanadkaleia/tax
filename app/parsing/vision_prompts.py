"""Form-specific prompt templates for Claude Vision extraction."""

from app.parsing.detector import FormType

SYSTEM_PROMPT = """\
You are a precise tax document data extractor. \
You extract structured data from images of U.S. tax forms.

Rules:
- Return ONLY valid JSON. No commentary, no markdown fences, no explanation.
- All monetary values as strings with exactly 2 decimal places (e.g., "250000.00").
- All dates in ISO format: YYYY-MM-DD.
- DO NOT include any PII (Personally Identifiable Information). Set SSN, EIN, TIN, and account number fields to null.
- If a field is not visible or not present on the form, set it to null.
- Be precise. Read each box label carefully and match values to the correct box number.
"""

FORM_DETECTION_PROMPT = """Look at these tax document page(s) and identify the form type.

Return JSON in this exact format:
{"form_type": "<type>"}

Where <type> is one of: "w2", "1099b", "1099div", "1099int", "3921", "3922"

IMPORTANT: This may be a COMPOSITE brokerage statement (e.g., from Robinhood, Morgan Stanley,
Schwab, Fidelity) that contains multiple form types (1099-B, 1099-DIV, 1099-INT) in one document.
If you see a composite/consolidated tax statement:
- If it contains 1099-B transaction data (proceeds, sales), return "1099b"
- If it only contains dividend data, return "1099div"
- If it only contains interest data, return "1099int"
- The first page may be a summary or cover page — look at ALL provided pages to determine the type.

If you cannot identify the form type, return:
{"form_type": null}
"""

W2_PROMPT = """Extract all data from this W-2 (Wage and Tax Statement) form image.

CRITICAL LAYOUT RULES:
1. Box 1 (Wages) and Box 2 (Federal income tax withheld) are in SEPARATE columns.
   Box 2 is always LESS than Box 1. Do NOT confuse them.
2. Read each digit carefully. Pay close attention to 8 vs 9, 3 vs 8, 5 vs 6.
   Double-check every monetary value against what is visually on the form.

BOX 12 vs BOX 14 — these are DIFFERENT sections, do NOT mix them up:
- BOX 12 (labeled "12a", "12b", "12c", "12d") is on the RIGHT side of the form.
  Box 12 entries have a single IRS code letter (1-2 chars) like C, D, DD, V, W, E, AA, BB.
  The valid codes are ONLY: A, AA, B, BB, C, D, DD, E, EE, F, FF, G, GG, H, HH,
  J, K, L, M, N, P, Q, R, S, T, V, W, Y, Z.
- BOX 14 (labeled "14 Other") is on the LEFT side, below Box 12.
  Box 14 entries have employer-defined text labels like RSU, ESPP, NSO, ISO, VPDI, SDI, NQSO.
  These are NOT IRS codes. They are free-text labels chosen by the employer.

If you see "RSU", "ESPP", "NSO", "ISO", "VPDI", "SDI", or "NQSO" next to a dollar amount,
those belong in box14_other, NOT in box12_codes.

Return JSON in this exact format:
{
  "tax_year": 2024,
  "employer_name": "Company Name",
  "employer_ein": null,
  "box1_wages": "250000.00",
  "box2_federal_withheld": "55000.00",
  "box3_ss_wages": "168600.00",
  "box4_ss_withheld": "10453.20",
  "box5_medicare_wages": "250000.00",
  "box6_medicare_withheld": "3625.00",
  "box12_codes": {"C": "405.08", "D": "12801.27", "DD": "8965.82"},
  "box14_other": {"RSU": "282417.52", "VPDI": "1760.00"},
  "box16_state_wages": "250000.00",
  "box17_state_withheld": "22000.00",
  "state": "CA"
}

Notes:
- box12_codes: ONLY IRS-defined 1-2 letter codes (C, D, DD, V, W, etc.) from boxes 12a-12d.
- box14_other: Employer-defined labels (RSU, ESPP, NSO, ISO, VPDI, SDI) from Box 14.
- Set employer_ein to null (PII).
- Set state to the two-letter state code shown on the form.
- Include all boxes you can read, set missing ones to null.
"""

FORM_1099B_PROMPT = """Extract all transaction records from this Form 1099-B (Proceeds From Broker and Barter Exchange Transactions).

IMPORTANT: This may be a composite brokerage statement with multiple sections (short-term covered,
short-term non-covered, long-term covered, long-term non-covered, etc.). Extract transactions from
ALL sections — do NOT skip any section or page.

Look for these common section headers and extract ALL transactions under each:
- "Short-Term Transactions for Which Basis Is Reported to the IRS" (basis_reported_to_irs: true)
- "Short-Term Transactions for Which Basis Is NOT Reported to the IRS" (basis_reported_to_irs: false)
- "Long-Term Transactions for Which Basis Is Reported to the IRS" (basis_reported_to_irs: true)
- "Long-Term Transactions for Which Basis Is NOT Reported to the IRS" (basis_reported_to_irs: false)

Return a JSON array of ALL transaction records across ALL pages and sections:
[
  {
    "tax_year": 2024,
    "broker_name": "Broker Name",
    "broker_source": "MANUAL",
    "description": "100 sh AAPL",
    "date_acquired": "2023-01-15",
    "date_sold": "2024-06-20",
    "proceeds": "15000.00",
    "cost_basis": "12000.00",
    "wash_sale_loss_disallowed": null,
    "basis_reported_to_irs": true
  }
]

Notes:
- Extract EVERY transaction row visible across ALL pages. Do not summarize or skip rows.
- If date_acquired shows "Various" or "VARIOUS", use the string "Various".
- cost_basis of "0.00" or missing means the broker did not report basis — set to null.
- basis_reported_to_irs: set based on the section header (see above).
- Set broker_source to "MANUAL" for all records.
- The broker_name should be the brokerage firm name (e.g., "Morgan Stanley", "Robinhood", etc.).
- For summary/total rows, do NOT include them — only extract individual transaction rows.
- If a page contains 1099-DIV or 1099-INT sections, IGNORE those — only extract 1099-B transactions.
"""

FORM_1099DIV_PROMPT = """Extract data from this Form 1099-DIV (Dividends and Distributions) image.

Return JSON in this exact format:
{
  "tax_year": 2024,
  "payer_name": "Fund Name",
  "ordinary_dividends": "1234.56",
  "qualified_dividends": "987.65",
  "capital_gain_distributions": "500.00",
  "nondividend_distributions": "0.00",
  "section_199a_dividends": "0.00",
  "foreign_tax_paid": "0.00",
  "foreign_country": null,
  "federal_tax_withheld": "0.00",
  "state_tax_withheld": "0.00"
}

Notes:
- ordinary_dividends = Box 1a
- qualified_dividends = Box 1b
- capital_gain_distributions = Box 2a
- nondividend_distributions = Box 3
- section_199a_dividends = Box 5
- foreign_tax_paid = Box 6 (Box 7 on older forms)
- foreign_country = Box 7 (Box 8 on older forms)
- federal_tax_withheld = Box 4
- state_tax_withheld = Box 14 (state income tax withheld)
- Set missing values to null.
"""

FORM_1099INT_PROMPT = """Extract data from this Form 1099-INT (Interest Income) image.

Return JSON in this exact format:
{
  "tax_year": 2024,
  "payer_name": "Bank Name",
  "interest_income": "456.78",
  "us_savings_bond_interest": "0.00",
  "early_withdrawal_penalty": "0.00",
  "federal_tax_withheld": "0.00",
  "state_tax_withheld": "0.00"
}

Notes:
- interest_income = Box 1
- early_withdrawal_penalty = Box 2
- us_savings_bond_interest = Box 3 (Interest on US Savings Bonds and Treasury obligations)
- federal_tax_withheld = Box 4
- state_tax_withheld = Box 15 (state income tax withheld)
- Set missing values to null.
"""

FORM_3921_PROMPT = """Extract data from this Form 3921 (Exercise of an Incentive Stock Option) image.

Return a JSON array (one record per exercise event, typically one):
[
  {
    "tax_year": 2024,
    "corporation_name": "Company Name",
    "grant_date": "2022-01-15",
    "exercise_date": "2024-03-01",
    "exercise_price_per_share": "50.00",
    "fmv_on_exercise_date": "120.00",
    "shares_transferred": 200
  }
]

Notes:
- grant_date = Box 1 (Date option was granted)
- exercise_date = Box 2 (Date option was exercised)
- exercise_price_per_share = Box 3
- fmv_on_exercise_date = Box 4 (FMV per share on exercise date)
- shares_transferred = Box 5 (integer, no decimals)
"""

FORM_3922_PROMPT = """Extract data from this Form 3922 (Transfer of Stock Acquired Through an ESPP) image.

Return a JSON array (one record per transfer event, typically one):
[
  {
    "tax_year": 2024,
    "corporation_name": "Company Name",
    "offering_date": "2024-01-01",
    "purchase_date": "2024-06-30",
    "fmv_on_offering_date": "140.00",
    "fmv_on_purchase_date": "150.00",
    "purchase_price_per_share": "127.50",
    "shares_transferred": 50
  }
]

Notes:
- offering_date = Box 1 (Date option was granted)
- purchase_date = Box 2 (Date option was exercised/transferred)
- fmv_on_offering_date = Box 3
- fmv_on_purchase_date = Box 4
- purchase_price_per_share = Box 5
- shares_transferred = Box 6 (integer, no decimals)
"""

FORM_PROMPTS: dict[FormType, str] = {
    FormType.W2: W2_PROMPT,
    FormType.FORM_1099B: FORM_1099B_PROMPT,
    FormType.FORM_1099DIV: FORM_1099DIV_PROMPT,
    FormType.FORM_1099INT: FORM_1099INT_PROMPT,
    FormType.FORM_3921: FORM_3921_PROMPT,
    FormType.FORM_3922: FORM_3922_PROMPT,
}
