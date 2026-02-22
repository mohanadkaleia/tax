"""Extractor factory for PDF form types."""

from app.parsing.base import BasePDFExtractor
from app.parsing.detector import FormType
from app.parsing.extractors.form_1099b import Form1099BExtractor
from app.parsing.extractors.form_1099div import Form1099DIVExtractor
from app.parsing.extractors.form_1099int import Form1099INTExtractor
from app.parsing.extractors.form_3921 import Form3921Extractor
from app.parsing.extractors.form_3922 import Form3922Extractor
from app.parsing.extractors.shareworks_rsu import ShareworksRSUExtractor
from app.parsing.extractors.w2 import W2Extractor

_EXTRACTOR_MAP: dict[FormType, type[BasePDFExtractor]] = {
    FormType.W2: W2Extractor,
    FormType.FORM_1099B: Form1099BExtractor,
    FormType.FORM_1099DIV: Form1099DIVExtractor,
    FormType.FORM_1099INT: Form1099INTExtractor,
    FormType.FORM_3921: Form3921Extractor,
    FormType.FORM_3922: Form3922Extractor,
    FormType.SHAREWORKS_RSU_RELEASE: ShareworksRSUExtractor,
}


def get_extractor(form_type: FormType) -> BasePDFExtractor:
    """Return the appropriate extractor instance for a form type."""
    extractor_cls = _EXTRACTOR_MAP[form_type]
    return extractor_cls()


__all__ = [
    "get_extractor",
    "Form1099BExtractor",
    "Form1099DIVExtractor",
    "Form1099INTExtractor",
    "Form3921Extractor",
    "Form3922Extractor",
    "ShareworksRSUExtractor",
    "W2Extractor",
]
