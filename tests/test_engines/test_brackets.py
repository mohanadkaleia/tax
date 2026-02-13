"""Tests for tax bracket data completeness and consistency."""

from decimal import Decimal

from app.engines.brackets import (
    AMT_28_PERCENT_THRESHOLD,
    AMT_EXEMPTION,
    AMT_PHASEOUT_START,
    CALIFORNIA_BRACKETS,
    CALIFORNIA_STANDARD_DEDUCTION,
    CAPITAL_LOSS_LIMIT,
    FEDERAL_BRACKETS,
    FEDERAL_LTCG_BRACKETS,
    FEDERAL_STANDARD_DEDUCTION,
    NIIT_THRESHOLD,
)
from app.models.enums import FilingStatus

ALL_STATUSES = [FilingStatus.SINGLE, FilingStatus.MFJ, FilingStatus.MFS, FilingStatus.HOH]


class TestFederalBrackets2024:
    def test_all_statuses_present(self):
        for status in ALL_STATUSES:
            assert status in FEDERAL_BRACKETS[2024], f"Missing {status}"

    def test_bracket_monotonicity(self):
        for status in ALL_STATUSES:
            brackets = FEDERAL_BRACKETS[2024][status]
            prev = Decimal("0")
            for upper, rate in brackets:
                if upper is not None:
                    assert upper > prev, f"Non-monotonic bracket for {status}: {upper} <= {prev}"
                    prev = upper

    def test_top_bracket_is_unbounded(self):
        for status in ALL_STATUSES:
            brackets = FEDERAL_BRACKETS[2024][status]
            assert brackets[-1][0] is None

    def test_single_known_values(self):
        brackets = FEDERAL_BRACKETS[2024][FilingStatus.SINGLE]
        assert brackets[0] == (Decimal("11600"), Decimal("0.10"))
        assert brackets[-1] == (None, Decimal("0.37"))


class TestFederalStandardDeduction2024:
    def test_all_statuses_present(self):
        for status in ALL_STATUSES:
            assert status in FEDERAL_STANDARD_DEDUCTION[2024], f"Missing {status}"

    def test_known_values(self):
        assert FEDERAL_STANDARD_DEDUCTION[2024][FilingStatus.SINGLE] == Decimal("14600")
        assert FEDERAL_STANDARD_DEDUCTION[2024][FilingStatus.MFJ] == Decimal("29200")
        assert FEDERAL_STANDARD_DEDUCTION[2024][FilingStatus.MFS] == Decimal("14600")
        assert FEDERAL_STANDARD_DEDUCTION[2024][FilingStatus.HOH] == Decimal("21900")


class TestLTCGBrackets2024:
    def test_all_statuses_present(self):
        for status in ALL_STATUSES:
            assert status in FEDERAL_LTCG_BRACKETS[2024], f"Missing {status}"

    def test_bracket_monotonicity(self):
        for status in ALL_STATUSES:
            brackets = FEDERAL_LTCG_BRACKETS[2024][status]
            prev = Decimal("0")
            for upper, rate in brackets:
                if upper is not None:
                    assert upper > prev
                    prev = upper

    def test_three_brackets(self):
        for status in ALL_STATUSES:
            assert len(FEDERAL_LTCG_BRACKETS[2024][status]) == 3

    def test_rates(self):
        for status in ALL_STATUSES:
            brackets = FEDERAL_LTCG_BRACKETS[2024][status]
            assert brackets[0][1] == Decimal("0.00")
            assert brackets[1][1] == Decimal("0.15")
            assert brackets[2][1] == Decimal("0.20")

    def test_single_known_values(self):
        brackets = FEDERAL_LTCG_BRACKETS[2024][FilingStatus.SINGLE]
        assert brackets[0][0] == Decimal("47025")
        assert brackets[1][0] == Decimal("518900")


class TestNIIT:
    def test_all_statuses_present(self):
        for status in ALL_STATUSES:
            assert status in NIIT_THRESHOLD, f"Missing {status}"

    def test_known_values(self):
        assert NIIT_THRESHOLD[FilingStatus.SINGLE] == Decimal("200000")
        assert NIIT_THRESHOLD[FilingStatus.MFJ] == Decimal("250000")
        assert NIIT_THRESHOLD[FilingStatus.MFS] == Decimal("125000")
        assert NIIT_THRESHOLD[FilingStatus.HOH] == Decimal("200000")


class TestAMT2024:
    def test_exemption_all_statuses(self):
        for status in ALL_STATUSES:
            assert status in AMT_EXEMPTION[2024], f"Missing exemption for {status}"

    def test_phaseout_all_statuses(self):
        for status in ALL_STATUSES:
            assert status in AMT_PHASEOUT_START[2024], f"Missing phaseout for {status}"

    def test_28_percent_threshold(self):
        assert 2024 in AMT_28_PERCENT_THRESHOLD
        assert AMT_28_PERCENT_THRESHOLD[2024] == Decimal("232600")

    def test_known_exemption_values(self):
        assert AMT_EXEMPTION[2024][FilingStatus.SINGLE] == Decimal("85700")
        assert AMT_EXEMPTION[2024][FilingStatus.MFJ] == Decimal("133300")
        assert AMT_EXEMPTION[2024][FilingStatus.MFS] == Decimal("66650")


class TestCaliforniaBrackets2024:
    def test_all_statuses_present(self):
        for status in ALL_STATUSES:
            assert status in CALIFORNIA_BRACKETS[2024], f"Missing {status}"

    def test_bracket_monotonicity(self):
        for status in ALL_STATUSES:
            brackets = CALIFORNIA_BRACKETS[2024][status]
            prev = Decimal("0")
            for upper, rate in brackets:
                if upper is not None:
                    assert upper > prev, f"Non-monotonic CA bracket for {status}: {upper} <= {prev}"
                    prev = upper

    def test_top_rate(self):
        for status in ALL_STATUSES:
            brackets = CALIFORNIA_BRACKETS[2024][status]
            assert brackets[-1][1] == Decimal("0.123")


class TestCaliforniaStandardDeduction2024:
    def test_all_statuses_present(self):
        for status in ALL_STATUSES:
            assert status in CALIFORNIA_STANDARD_DEDUCTION[2024], f"Missing {status}"

    def test_known_values(self):
        assert CALIFORNIA_STANDARD_DEDUCTION[2024][FilingStatus.SINGLE] == Decimal("5540")
        assert CALIFORNIA_STANDARD_DEDUCTION[2024][FilingStatus.MFJ] == Decimal("11080")


class TestCapitalLossLimit:
    def test_all_statuses_present(self):
        for status in ALL_STATUSES:
            assert status in CAPITAL_LOSS_LIMIT, f"Missing {status}"

    def test_mfs_limit(self):
        assert CAPITAL_LOSS_LIMIT[FilingStatus.MFS] == Decimal("1500")

    def test_other_limits(self):
        assert CAPITAL_LOSS_LIMIT[FilingStatus.SINGLE] == Decimal("3000")
        assert CAPITAL_LOSS_LIMIT[FilingStatus.MFJ] == Decimal("3000")
        assert CAPITAL_LOSS_LIMIT[FilingStatus.HOH] == Decimal("3000")
