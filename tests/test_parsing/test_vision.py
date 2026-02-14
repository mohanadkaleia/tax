"""Tests for VisionExtractor (Claude Vision API extraction)."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from app.parsing.detector import FormType
from app.parsing.vision_prompts import (
    FORM_PROMPTS,
    SYSTEM_PROMPT,
)

# ---- Fixtures ----

@pytest.fixture()
def mock_api_key(monkeypatch):
    """Set a fake API key for testing."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-12345")


@pytest.fixture()
def sample_w2_response():
    return json.dumps({
        "tax_year": 2024,
        "employer_name": "Acme Corp",
        "employer_ein": None,
        "box1_wages": "250000.00",
        "box2_federal_withheld": "55000.00",
        "box3_ss_wages": "168600.00",
        "box4_ss_withheld": "10453.20",
        "box5_medicare_wages": "250000.00",
        "box6_medicare_withheld": "3625.00",
        "box12_codes": {"V": "5000.00"},
        "box14_other": {"RSU": "50000.00", "ESPP": "3000.00"},
        "box16_state_wages": "250000.00",
        "box17_state_withheld": "22000.00",
        "state": "CA",
    })


@pytest.fixture()
def sample_1099b_response():
    return json.dumps([
        {
            "tax_year": 2024,
            "broker_name": "Morgan Stanley",
            "broker_source": "MANUAL",
            "description": "100 sh AAPL",
            "date_acquired": "2023-01-15",
            "date_sold": "2024-06-20",
            "proceeds": "15000.00",
            "cost_basis": "12000.00",
            "wash_sale_loss_disallowed": None,
            "basis_reported_to_irs": True,
        }
    ])


@pytest.fixture()
def sample_detection_response():
    return json.dumps({"form_type": "w2"})


@pytest.fixture()
def fake_png_bytes():
    """Minimal PNG bytes for testing (1x1 white pixel)."""
    import struct
    import zlib

    def _chunk(chunk_type, data):
        c = chunk_type + data
        crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + c + crc

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw = zlib.compress(b"\x00\xff\xff\xff")
    idat = _chunk(b"IDAT", raw)
    iend = _chunk(b"IEND", b"")
    return header + ihdr + idat + iend


def _mock_anthropic_client(response_text):
    """Create a mock Anthropic client that returns the given text."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_content_block = MagicMock()
    mock_content_block.text = response_text
    mock_response.content = [mock_content_block]
    mock_client.messages.create.return_value = mock_response
    return mock_client


# ---- Unit Tests (mocked API) ----

class TestVisionExtractorUnit:
    def test_init_requires_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        from app.exceptions import VisionExtractionError
        from app.parsing.vision import VisionExtractor
        with pytest.raises(VisionExtractionError, match="ANTHROPIC_API_KEY"):
            VisionExtractor()

    def test_init_with_env_key(self, mock_api_key):
        from app.parsing.vision import VisionExtractor
        extractor = VisionExtractor()
        assert extractor._api_key == "test-key-12345"

    def test_init_with_explicit_key(self):
        from app.parsing.vision import VisionExtractor
        extractor = VisionExtractor(api_key="explicit-key")
        assert extractor._api_key == "explicit-key"

    def test_detect_form_type(self, mock_api_key, sample_detection_response, fake_png_bytes):
        from app.parsing.vision import VisionExtractor
        extractor = VisionExtractor()
        extractor._client = _mock_anthropic_client(sample_detection_response)

        result = extractor.detect_form_type([fake_png_bytes])
        assert result == FormType.W2

    def test_detect_form_type_unknown(self, mock_api_key, fake_png_bytes):
        from app.parsing.vision import VisionExtractor
        extractor = VisionExtractor()
        extractor._client = _mock_anthropic_client('{"form_type": null}')

        result = extractor.detect_form_type([fake_png_bytes])
        assert result is None

    def test_extract_w2(self, mock_api_key, sample_w2_response, fake_png_bytes):
        from app.parsing.vision import VisionExtractor
        extractor = VisionExtractor()
        extractor._client = _mock_anthropic_client(sample_w2_response)

        data = extractor.extract([fake_png_bytes], FormType.W2)
        assert isinstance(data, dict)
        assert data["box1_wages"] == "250000.00"
        assert data["box2_federal_withheld"] == "55000.00"
        assert data["employer_name"] == "Acme Corp"
        assert data["employer_ein"] is None
        assert data["box12_codes"]["V"] == "5000.00"

    def test_extract_1099b(self, mock_api_key, sample_1099b_response, fake_png_bytes):
        from app.parsing.vision import VisionExtractor
        extractor = VisionExtractor()
        extractor._client = _mock_anthropic_client(sample_1099b_response)

        data = extractor.extract([fake_png_bytes], FormType.FORM_1099B)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["proceeds"] == "15000.00"
        assert data[0]["description"] == "100 sh AAPL"

    def test_parse_json_with_markdown_fences(self, mock_api_key):
        from app.parsing.vision import VisionExtractor
        response = '```json\n{"tax_year": 2024, "box1_wages": "100.00"}\n```'
        result = VisionExtractor._parse_json_response(response)
        assert result == {"tax_year": 2024, "box1_wages": "100.00"}

    def test_parse_json_with_surrounding_text(self, mock_api_key):
        from app.parsing.vision import VisionExtractor
        response = 'Here is the data:\n{"tax_year": 2024}\nEnd of data.'
        result = VisionExtractor._parse_json_response(response)
        assert result == {"tax_year": 2024}

    def test_parse_json_returns_none_for_garbage(self, mock_api_key):
        from app.parsing.vision import VisionExtractor
        result = VisionExtractor._parse_json_response("this is not json at all")
        assert result is None

    def test_extract_raises_on_bad_json(self, mock_api_key, fake_png_bytes):
        from app.exceptions import VisionExtractionError
        from app.parsing.vision import VisionExtractor
        extractor = VisionExtractor()
        extractor._client = _mock_anthropic_client("I cannot process this image.")

        with pytest.raises(VisionExtractionError, match="no parseable JSON"):
            extractor.extract([fake_png_bytes], FormType.W2)

    def test_retry_on_rate_limit(self, mock_api_key, sample_w2_response, fake_png_bytes):
        from app.parsing.vision import VisionExtractor
        extractor = VisionExtractor()

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_content_block = MagicMock()
        mock_content_block.text = sample_w2_response
        mock_response.content = [mock_content_block]

        # First call raises 429, second succeeds
        mock_client.messages.create.side_effect = [
            Exception("Error code: 429 rate limit exceeded"),
            mock_response,
        ]
        extractor._client = mock_client

        with patch("app.parsing.vision.time.sleep"):  # skip actual sleep
            data = extractor.extract([fake_png_bytes], FormType.W2)
        assert data["box1_wages"] == "250000.00"
        assert mock_client.messages.create.call_count == 2

    def test_all_retries_exhausted(self, mock_api_key, fake_png_bytes):
        from app.exceptions import VisionExtractionError
        from app.parsing.vision import VisionExtractor
        extractor = VisionExtractor()

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("Error code: 500 internal server error")
        extractor._client = mock_client

        with patch("app.parsing.vision.time.sleep"):
            with pytest.raises(VisionExtractionError, match="API call failed after 3 attempts"):
                extractor.extract([fake_png_bytes], FormType.W2)

    def test_non_retryable_error_fails_immediately(self, mock_api_key, fake_png_bytes):
        from app.exceptions import VisionExtractionError
        from app.parsing.vision import VisionExtractor
        extractor = VisionExtractor()

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("Invalid API key")
        extractor._client = mock_client

        with pytest.raises(VisionExtractionError, match="API call failed"):
            extractor.extract([fake_png_bytes], FormType.W2)
        assert mock_client.messages.create.call_count == 1

    def test_vision_output_passes_w2_validation(self, mock_api_key, sample_w2_response, fake_png_bytes):
        """Vision output should pass the same validation as regex extractors."""
        from app.parsing.extractors import get_extractor
        from app.parsing.vision import VisionExtractor

        extractor = VisionExtractor()
        extractor._client = _mock_anthropic_client(sample_w2_response)
        data = extractor.extract([fake_png_bytes], FormType.W2)

        regex_extractor = get_extractor(FormType.W2)
        errors = regex_extractor.validate_extraction(data)
        assert errors == []

    def test_vision_output_passes_1099b_validation(self, mock_api_key, sample_1099b_response, fake_png_bytes):
        from app.parsing.extractors import get_extractor
        from app.parsing.vision import VisionExtractor

        extractor = VisionExtractor()
        extractor._client = _mock_anthropic_client(sample_1099b_response)
        data = extractor.extract([fake_png_bytes], FormType.FORM_1099B)

        regex_extractor = get_extractor(FormType.FORM_1099B)
        errors = regex_extractor.validate_extraction(data)
        assert errors == []


class TestVisionPrompts:
    def test_all_form_types_have_prompts(self):
        # EQUITY_LOTS and SHAREWORKS_SUPPLEMENTAL are manual/adapter-only, no vision prompt needed
        skip = {FormType.EQUITY_LOTS, FormType.SHAREWORKS_SUPPLEMENTAL}
        for ft in FormType:
            if ft in skip:
                continue
            assert ft in FORM_PROMPTS, f"Missing vision prompt for {ft.value}"

    def test_system_prompt_mentions_json(self):
        assert "JSON" in SYSTEM_PROMPT

    def test_system_prompt_mentions_pii(self):
        assert "PII" in SYSTEM_PROMPT

    def test_w2_prompt_warns_about_column_confusion(self):
        assert "Box 1" in FORM_PROMPTS[FormType.W2]
        assert "Box 2" in FORM_PROMPTS[FormType.W2]
        assert "column" in FORM_PROMPTS[FormType.W2].lower()


class TestPdfToImages:
    def test_pdf_to_images(self, w2_pdf, mock_api_key):
        from app.parsing.vision import VisionExtractor
        images = VisionExtractor.pdf_to_images(w2_pdf)
        assert len(images) == 1
        # Verify it's a JPEG (magic bytes) — images are JPEG-compressed to fit API limits
        assert images[0][:2] == b"\xff\xd8"


# ---- Integration Tests (live API, skipped without key) ----

@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set — skipping live API test",
)
class TestVisionExtractorIntegration:
    def test_live_w2_extraction(self, w2_pdf):
        """Integration test: extract W-2 data via live Claude Vision API."""
        from app.parsing.vision import VisionExtractor

        extractor = VisionExtractor()
        images = extractor.pdf_to_images(w2_pdf)
        form_type = extractor.detect_form_type(images)
        assert form_type == FormType.W2

        data = extractor.extract(images, FormType.W2)
        assert isinstance(data, dict)
        assert "box1_wages" in data
        assert "box2_federal_withheld" in data
