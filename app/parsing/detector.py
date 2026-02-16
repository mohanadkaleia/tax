"""Form type auto-detection from PDF text."""

from enum import StrEnum


class FormType(StrEnum):
    """Supported PDF tax form types."""

    W2 = "w2"
    FORM_1099B = "1099b"
    FORM_1099DIV = "1099div"
    FORM_1099INT = "1099int"
    FORM_3921 = "3921"
    FORM_3922 = "3922"
    SHAREWORKS_SUPPLEMENTAL = "shareworks_supplemental"
    ROBINHOOD_CONSOLIDATED = "robinhood_consolidated"
    EQUITY_LOTS = "equity_lots"


# Ordered by specificity (most specific first to avoid false matches)
FORM_SIGNATURES: list[tuple[FormType, list[str]]] = [
    (FormType.FORM_3921, ["Form 3921", "Exercise of an Incentive Stock Option"]),
    (FormType.FORM_3922, ["Form 3922", "Transfer of Stock Acquired", "Employee Stock Purchase Plan"]),
    (FormType.FORM_1099B, ["Form 1099-B", "Proceeds From Broker"]),
    (FormType.FORM_1099DIV, ["Form 1099-DIV", "Dividends and Distributions"]),
    (FormType.FORM_1099INT, ["Form 1099-INT", "Interest Income"]),
    (FormType.W2, ["Form W-2", "Wage and Tax Statement"]),
]


def detect_form_type(text: str) -> FormType | None:
    """Auto-detect form type from extracted PDF text.

    Returns the FormType if detected, None if unrecognized.
    """
    text_upper = text.upper()
    for form_type, signatures in FORM_SIGNATURES:
        if any(sig.upper() in text_upper for sig in signatures):
            return form_type
    return None
