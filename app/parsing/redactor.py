"""PII detection and redaction for PDF extracted text."""

import re
from dataclasses import dataclass, field


@dataclass
class RedactionResult:
    """Result of PII redaction."""

    text: str
    redactions_made: list[str] = field(default_factory=list)


class Redactor:
    """Detects and removes PII from extracted text."""

    PII_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
        ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "***-**-****"),
        ("EIN", re.compile(r"\b\d{2}-\d{7}\b"), "**-*******"),
        (
            "ACCOUNT_NUM",
            re.compile(r"(Account\s*(?:Number|No\.?)\s*:?\s*)([\w\-]+)", re.IGNORECASE),
            r"\1[REDACTED]",
        ),
        (
            "RECIPIENT_TIN",
            re.compile(r"(Recipient'?s?\s*(?:TIN|identification\s*number)\s*:?\s*)([\d\-*]+)", re.IGNORECASE),
            r"\1[REDACTED]",
        ),
        (
            "PAYER_TIN",
            re.compile(r"(Payer'?s?\s*(?:TIN|identification\s*number)\s*:?\s*)([\d\-*]+)", re.IGNORECASE),
            r"\1[REDACTED]",
        ),
    ]

    def redact(self, text: str) -> RedactionResult:
        """Redact all PII patterns from text."""
        redactions: list[str] = []
        result = text
        for name, pattern, replacement in self.PII_PATTERNS:
            matches = pattern.findall(result)
            if matches:
                count = len(matches)
                redactions.append(f"{name}: {count} occurrence(s) redacted")
                result = pattern.sub(replacement, result)
        return RedactionResult(text=result, redactions_made=redactions)

    def scrub_output(self, data: dict) -> dict:
        """Remove PII fields from the final output dictionary."""
        scrubbed = data.copy()
        if "employer_ein" in scrubbed:
            scrubbed["employer_ein"] = None
        return scrubbed
