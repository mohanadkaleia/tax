"""Enumerations for EquityTax Reconciler."""

from enum import StrEnum


class EquityType(StrEnum):
    RSU = "RSU"
    ISO = "ISO"
    NSO = "NSO"
    ESPP = "ESPP"


class TransactionType(StrEnum):
    VEST = "VEST"
    EXERCISE = "EXERCISE"
    PURCHASE = "PURCHASE"
    SALE = "SALE"
    DIVIDEND = "DIVIDEND"
    INTEREST = "INTEREST"


class DispositionType(StrEnum):
    QUALIFYING = "QUALIFYING"
    DISQUALIFYING = "DISQUALIFYING"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class HoldingPeriod(StrEnum):
    SHORT_TERM = "SHORT_TERM"
    LONG_TERM = "LONG_TERM"


class Form8949Category(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"


class AdjustmentCode(StrEnum):
    B = "B"
    E = "e"
    OTHER = "O"  # noqa: E741
    NONE = ""


class FilingStatus(StrEnum):
    SINGLE = "SINGLE"
    MFJ = "MARRIED_FILING_JOINTLY"
    MFS = "MARRIED_FILING_SEPARATELY"
    HOH = "HEAD_OF_HOUSEHOLD"


class BrokerSource(StrEnum):
    SHAREWORKS = "SHAREWORKS"
    ROBINHOOD = "ROBINHOOD"
    MANUAL = "MANUAL"
