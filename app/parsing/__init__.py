"""PDF parsing and extraction for TaxBot 9000."""

from app.parsing.detector import FormType, detect_form_type
from app.parsing.redactor import Redactor

__all__ = [
    "FormType",
    "Redactor",
    "VisionExtractor",
    "detect_form_type",
]


def __getattr__(name: str):
    """Lazy import VisionExtractor to avoid requiring anthropic at import time."""
    if name == "VisionExtractor":
        from app.parsing.vision import VisionExtractor
        return VisionExtractor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
