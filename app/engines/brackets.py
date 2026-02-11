"""Tax bracket configuration.

Federal and California tax brackets, standard deductions, and thresholds.
Keyed by tax year and filing status. Never hardcode brackets in computation functions.
"""

from decimal import Decimal

from app.models.enums import FilingStatus

# Federal ordinary income brackets: {year: {filing_status: [(upper_bound, rate), ...]}}
# Upper bound is Decimal or None for the top bracket.
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

# Federal standard deduction
FEDERAL_STANDARD_DEDUCTION: dict[int, dict[FilingStatus, Decimal]] = {
    2024: {
        FilingStatus.SINGLE: Decimal("14600"),
        FilingStatus.MFJ: Decimal("29200"),
    },
    2025: {
        FilingStatus.SINGLE: Decimal("15000"),
        FilingStatus.MFJ: Decimal("30000"),
    },
}

# Federal LTCG rate brackets: (upper_bound, rate)
FEDERAL_LTCG_BRACKETS: dict[int, dict[FilingStatus, list[tuple[Decimal | None, Decimal]]]] = {
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

# NIIT thresholds
NIIT_RATE = Decimal("0.038")
NIIT_THRESHOLD: dict[FilingStatus, Decimal] = {
    FilingStatus.SINGLE: Decimal("200000"),
    FilingStatus.MFJ: Decimal("250000"),
}

# AMT exemption amounts
AMT_EXEMPTION: dict[int, dict[FilingStatus, Decimal]] = {
    2025: {
        FilingStatus.SINGLE: Decimal("85700"),
        FilingStatus.MFJ: Decimal("133300"),
    },
}

AMT_PHASEOUT_START: dict[int, dict[FilingStatus, Decimal]] = {
    2025: {
        FilingStatus.SINGLE: Decimal("609350"),
        FilingStatus.MFJ: Decimal("1218700"),
    },
}

# California brackets (2025, Single)
CALIFORNIA_BRACKETS: dict[int, dict[FilingStatus, list[tuple[Decimal | None, Decimal]]]] = {
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
            (Decimal("1000000"), Decimal("0.123")),
            (None, Decimal("0.133")),
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
            (Decimal("1000000"), Decimal("0.123")),
            (None, Decimal("0.133")),
        ],
    },
}

# California standard deduction
CALIFORNIA_STANDARD_DEDUCTION: dict[int, dict[FilingStatus, Decimal]] = {
    2025: {
        FilingStatus.SINGLE: Decimal("5540"),
        FilingStatus.MFJ: Decimal("11080"),
    },
}

# California Mental Health Services Tax: 1% on income above $1M
CA_MENTAL_HEALTH_THRESHOLD = Decimal("1000000")
CA_MENTAL_HEALTH_RATE = Decimal("0.01")
