"""Tax bracket configuration.

Federal and California tax brackets, standard deductions, and thresholds.
Keyed by tax year and filing status. Never hardcode brackets in computation functions.

Sources:
  - 2024: IRS Rev. Proc. 2023-34, FTB Publication 1001 (2024)
  - 2025: IRS Rev. Proc. 2024-40, FTB Publication 1001 (2025)
"""

from decimal import Decimal

from app.models.enums import FilingStatus

# ---------------------------------------------------------------------------
# Federal ordinary income brackets: {year: {filing_status: [(upper_bound, rate), ...]}}
# Upper bound is Decimal or None for the top bracket.
# ---------------------------------------------------------------------------
FEDERAL_BRACKETS: dict[int, dict[FilingStatus, list[tuple[Decimal | None, Decimal]]]] = {
    2024: {
        FilingStatus.SINGLE: [
            (Decimal("11600"), Decimal("0.10")),
            (Decimal("47150"), Decimal("0.12")),
            (Decimal("100525"), Decimal("0.22")),
            (Decimal("191950"), Decimal("0.24")),
            (Decimal("243725"), Decimal("0.32")),
            (Decimal("609350"), Decimal("0.35")),
            (None, Decimal("0.37")),
        ],
        FilingStatus.MFJ: [
            (Decimal("23200"), Decimal("0.10")),
            (Decimal("94300"), Decimal("0.12")),
            (Decimal("201050"), Decimal("0.22")),
            (Decimal("383900"), Decimal("0.24")),
            (Decimal("487450"), Decimal("0.32")),
            (Decimal("731200"), Decimal("0.35")),
            (None, Decimal("0.37")),
        ],
        FilingStatus.MFS: [
            (Decimal("11600"), Decimal("0.10")),
            (Decimal("47150"), Decimal("0.12")),
            (Decimal("100525"), Decimal("0.22")),
            (Decimal("191950"), Decimal("0.24")),
            (Decimal("243725"), Decimal("0.32")),
            (Decimal("365600"), Decimal("0.35")),
            (None, Decimal("0.37")),
        ],
        FilingStatus.HOH: [
            (Decimal("16550"), Decimal("0.10")),
            (Decimal("63100"), Decimal("0.12")),
            (Decimal("100500"), Decimal("0.22")),
            (Decimal("191950"), Decimal("0.24")),
            (Decimal("243700"), Decimal("0.32")),
            (Decimal("609350"), Decimal("0.35")),
            (None, Decimal("0.37")),
        ],
    },
    2025: {
        FilingStatus.SINGLE: [
            (Decimal("11925"), Decimal("0.10")),
            (Decimal("48475"), Decimal("0.12")),
            (Decimal("103350"), Decimal("0.22")),
            (Decimal("197300"), Decimal("0.24")),
            (Decimal("250525"), Decimal("0.32")),
            (Decimal("626350"), Decimal("0.35")),
            (None, Decimal("0.37")),
        ],
        FilingStatus.MFJ: [
            (Decimal("23850"), Decimal("0.10")),
            (Decimal("96950"), Decimal("0.12")),
            (Decimal("206700"), Decimal("0.22")),
            (Decimal("394600"), Decimal("0.24")),
            (Decimal("501050"), Decimal("0.32")),
            (Decimal("751600"), Decimal("0.35")),
            (None, Decimal("0.37")),
        ],
    },
}

# ---------------------------------------------------------------------------
# Federal standard deduction
# ---------------------------------------------------------------------------
FEDERAL_STANDARD_DEDUCTION: dict[int, dict[FilingStatus, Decimal]] = {
    2024: {
        FilingStatus.SINGLE: Decimal("14600"),
        FilingStatus.MFJ: Decimal("29200"),
        FilingStatus.MFS: Decimal("14600"),
        FilingStatus.HOH: Decimal("21900"),
    },
    2025: {
        FilingStatus.SINGLE: Decimal("15000"),
        FilingStatus.MFJ: Decimal("30000"),
    },
}

# ---------------------------------------------------------------------------
# Federal LTCG rate brackets: (upper_bound, rate)
# These are the taxable-income thresholds for the 0%/15%/20% rates.
# Per IRC Section 1(h) and IRS Rev. Proc. 2023-34.
# ---------------------------------------------------------------------------
FEDERAL_LTCG_BRACKETS: dict[int, dict[FilingStatus, list[tuple[Decimal | None, Decimal]]]] = {
    2024: {
        FilingStatus.SINGLE: [
            (Decimal("47025"), Decimal("0.00")),
            (Decimal("518900"), Decimal("0.15")),
            (None, Decimal("0.20")),
        ],
        FilingStatus.MFJ: [
            (Decimal("94050"), Decimal("0.00")),
            (Decimal("583750"), Decimal("0.15")),
            (None, Decimal("0.20")),
        ],
        FilingStatus.MFS: [
            (Decimal("47025"), Decimal("0.00")),
            (Decimal("291850"), Decimal("0.15")),
            (None, Decimal("0.20")),
        ],
        FilingStatus.HOH: [
            (Decimal("63000"), Decimal("0.00")),
            (Decimal("551350"), Decimal("0.15")),
            (None, Decimal("0.20")),
        ],
    },
    2025: {
        FilingStatus.SINGLE: [
            (Decimal("48475"), Decimal("0.00")),
            (Decimal("533400"), Decimal("0.15")),
            (None, Decimal("0.20")),
        ],
        FilingStatus.MFJ: [
            (Decimal("96950"), Decimal("0.00")),
            (Decimal("600050"), Decimal("0.15")),
            (None, Decimal("0.20")),
        ],
    },
}

# ---------------------------------------------------------------------------
# NIIT thresholds (IRC Section 1411)
# Thresholds are NOT inflation-adjusted — statutory amounts.
# ---------------------------------------------------------------------------
NIIT_RATE = Decimal("0.038")
NIIT_THRESHOLD: dict[FilingStatus, Decimal] = {
    FilingStatus.SINGLE: Decimal("200000"),
    FilingStatus.MFJ: Decimal("250000"),
    FilingStatus.MFS: Decimal("125000"),
    FilingStatus.HOH: Decimal("200000"),
}

# ---------------------------------------------------------------------------
# AMT exemption amounts (IRS Rev. Proc. 2023-34 for 2024)
# ---------------------------------------------------------------------------
AMT_EXEMPTION: dict[int, dict[FilingStatus, Decimal]] = {
    2024: {
        FilingStatus.SINGLE: Decimal("85700"),
        FilingStatus.MFJ: Decimal("133300"),
        FilingStatus.MFS: Decimal("66650"),
        FilingStatus.HOH: Decimal("85700"),
    },
    2025: {
        FilingStatus.SINGLE: Decimal("88100"),
        FilingStatus.MFJ: Decimal("137000"),
    },
}

# AMT exemption phase-out start
AMT_PHASEOUT_START: dict[int, dict[FilingStatus, Decimal]] = {
    2024: {
        FilingStatus.SINGLE: Decimal("609350"),
        FilingStatus.MFJ: Decimal("1218700"),
        FilingStatus.MFS: Decimal("609350"),
        FilingStatus.HOH: Decimal("609350"),
    },
    2025: {
        FilingStatus.SINGLE: Decimal("626350"),
        FilingStatus.MFJ: Decimal("1252700"),
    },
}

# AMT 28% threshold — applies to all filing statuses (except MFS gets half)
AMT_28_PERCENT_THRESHOLD: dict[int, Decimal] = {
    2024: Decimal("232600"),
    2025: Decimal("239100"),
}

# ---------------------------------------------------------------------------
# Additional Medicare Tax (IRC Section 3101(b)(2)) — 0.9% on wages exceeding threshold
# Thresholds are NOT inflation-adjusted — statutory amounts.
# ---------------------------------------------------------------------------
ADDITIONAL_MEDICARE_TAX_RATE = Decimal("0.009")
ADDITIONAL_MEDICARE_TAX_THRESHOLD: dict[FilingStatus, Decimal] = {
    FilingStatus.SINGLE: Decimal("200000"),
    FilingStatus.MFJ: Decimal("250000"),
    FilingStatus.MFS: Decimal("125000"),
    FilingStatus.HOH: Decimal("200000"),
}
REGULAR_MEDICARE_TAX_RATE = Decimal("0.0145")  # 1.45% regular rate

# ---------------------------------------------------------------------------
# Capital loss limitation per IRC Section 1211(b)
# ---------------------------------------------------------------------------
CAPITAL_LOSS_LIMIT: dict[FilingStatus, Decimal] = {
    FilingStatus.SINGLE: Decimal("3000"),
    FilingStatus.MFJ: Decimal("3000"),
    FilingStatus.MFS: Decimal("1500"),
    FilingStatus.HOH: Decimal("3000"),
}

# ---------------------------------------------------------------------------
# California brackets (2024) — CA Revenue and Taxation Code Section 17041
# FTB Publication 1001, 2024 Tax Rates
# ---------------------------------------------------------------------------
CALIFORNIA_BRACKETS: dict[int, dict[FilingStatus, list[tuple[Decimal | None, Decimal]]]] = {
    2024: {
        FilingStatus.SINGLE: [
            (Decimal("10412"), Decimal("0.01")),
            (Decimal("24684"), Decimal("0.02")),
            (Decimal("38959"), Decimal("0.04")),
            (Decimal("54081"), Decimal("0.06")),
            (Decimal("68350"), Decimal("0.08")),
            (Decimal("349137"), Decimal("0.093")),
            (Decimal("418961"), Decimal("0.103")),
            (Decimal("698271"), Decimal("0.113")),
            (None, Decimal("0.123")),
        ],
        FilingStatus.MFJ: [
            (Decimal("20824"), Decimal("0.01")),
            (Decimal("49368"), Decimal("0.02")),
            (Decimal("77918"), Decimal("0.04")),
            (Decimal("108162"), Decimal("0.06")),
            (Decimal("136700"), Decimal("0.08")),
            (Decimal("698274"), Decimal("0.093")),
            (Decimal("837922"), Decimal("0.103")),
            (Decimal("1396542"), Decimal("0.113")),
            (None, Decimal("0.123")),
        ],
        FilingStatus.MFS: [
            (Decimal("10412"), Decimal("0.01")),
            (Decimal("24684"), Decimal("0.02")),
            (Decimal("38959"), Decimal("0.04")),
            (Decimal("54081"), Decimal("0.06")),
            (Decimal("68350"), Decimal("0.08")),
            (Decimal("349137"), Decimal("0.093")),
            (Decimal("418961"), Decimal("0.103")),
            (Decimal("698271"), Decimal("0.113")),
            (None, Decimal("0.123")),
        ],
        FilingStatus.HOH: [
            (Decimal("20839"), Decimal("0.01")),
            (Decimal("49371"), Decimal("0.02")),
            (Decimal("63644"), Decimal("0.04")),
            (Decimal("78765"), Decimal("0.06")),
            (Decimal("93037"), Decimal("0.08")),
            (Decimal("474824"), Decimal("0.093")),
            (Decimal("569790"), Decimal("0.103")),
            (Decimal("949649"), Decimal("0.113")),
            (None, Decimal("0.123")),
        ],
    },
    2025: {
        FilingStatus.SINGLE: [
            (Decimal("10412"), Decimal("0.01")),
            (Decimal("24684"), Decimal("0.02")),
            (Decimal("38959"), Decimal("0.04")),
            (Decimal("54081"), Decimal("0.06")),
            (Decimal("68350"), Decimal("0.08")),
            (Decimal("349137"), Decimal("0.093")),
            (Decimal("418961"), Decimal("0.103")),
            (Decimal("698271"), Decimal("0.113")),
            (None, Decimal("0.123")),
        ],
        FilingStatus.MFJ: [
            (Decimal("20824"), Decimal("0.01")),
            (Decimal("49368"), Decimal("0.02")),
            (Decimal("77918"), Decimal("0.04")),
            (Decimal("108162"), Decimal("0.06")),
            (Decimal("136700"), Decimal("0.08")),
            (Decimal("698274"), Decimal("0.093")),
            (Decimal("837922"), Decimal("0.103")),
            (Decimal("1396542"), Decimal("0.113")),
            (None, Decimal("0.123")),
        ],
    },
}

# ---------------------------------------------------------------------------
# California standard deduction (FTB Publication 1001)
# ---------------------------------------------------------------------------
CALIFORNIA_STANDARD_DEDUCTION: dict[int, dict[FilingStatus, Decimal]] = {
    2024: {
        FilingStatus.SINGLE: Decimal("5540"),
        FilingStatus.MFJ: Decimal("11080"),
        FilingStatus.MFS: Decimal("5540"),
        FilingStatus.HOH: Decimal("11080"),
    },
    2025: {
        FilingStatus.SINGLE: Decimal("5540"),
        FilingStatus.MFJ: Decimal("11080"),
    },
}

# ---------------------------------------------------------------------------
# California Mental Health Services Tax: 1% on income above $1M
# CA Revenue and Taxation Code Section 17043(a)
# ---------------------------------------------------------------------------
CA_MENTAL_HEALTH_THRESHOLD = Decimal("1000000")
CA_MENTAL_HEALTH_RATE = Decimal("0.01")

# ---------------------------------------------------------------------------
# SALT cap per IRC Section 164(b)(6) — TCJA 2018-2025
# ---------------------------------------------------------------------------
FEDERAL_SALT_CAP: dict[int, dict[FilingStatus, Decimal]] = {
    2024: {
        FilingStatus.SINGLE: Decimal("10000"),
        FilingStatus.MFJ: Decimal("10000"),
        FilingStatus.MFS: Decimal("5000"),
        FilingStatus.HOH: Decimal("10000"),
    },
    2025: {
        FilingStatus.SINGLE: Decimal("10000"),
        FilingStatus.MFJ: Decimal("10000"),
        FilingStatus.MFS: Decimal("5000"),
        FilingStatus.HOH: Decimal("10000"),
    },
}

# ---------------------------------------------------------------------------
# Charitable contribution AGI limits (IRC Section 170(b))
# ---------------------------------------------------------------------------
CHARITABLE_CASH_AGI_LIMIT = Decimal("0.60")  # 60% of AGI for cash to public charities
CHARITABLE_PROPERTY_AGI_LIMIT = Decimal("0.30")  # 30% of AGI for appreciated property

# ---------------------------------------------------------------------------
# Medical expense AGI floor (IRC Section 213(a))
# ---------------------------------------------------------------------------
MEDICAL_EXPENSE_AGI_FLOOR = Decimal("0.075")  # 7.5% of AGI
