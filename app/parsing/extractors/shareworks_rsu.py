"""Shareworks RSU Releases Report PDF extractor."""

from typing import Any

from app.parsing.base import BasePDFExtractor


class ShareworksRSUExtractor(BasePDFExtractor):
    """Extracts RSU vest records from Shareworks Releases Report PDF text.

    The Releases Report is a complex table layout that pdfplumber typically
    cannot extract cleanly. This extractor attempts text-based extraction
    but will usually return empty, triggering the Vision API fallback.
    """

    def extract(self, text: str, tables: list[list[list[str]]] | None = None) -> list[dict[str, Any]]:
        """Attempt text-based extraction from Releases Report.

        Returns empty list for scanned/complex PDFs, triggering Vision fallback.
        """
        # The Shareworks Releases Report has a complex multi-row table layout
        # that pdfplumber cannot reliably extract. Return empty to trigger
        # Vision API fallback in _process_pdf().
        return []

    def validate_extraction(self, data: dict[str, Any] | list[dict[str, Any]]) -> list[str]:
        """Validate Shareworks RSU release extraction."""
        records = data if isinstance(data, list) else [data]
        errors: list[str] = []
        required = ["vest_date", "release_price", "shares_vested", "shares_net"]
        for i, record in enumerate(records):
            for field in required:
                if field not in record or record[field] is None:
                    errors.append(
                        f"Record {i + 1}: Missing required Shareworks RSU field: {field}"
                    )
        return errors
