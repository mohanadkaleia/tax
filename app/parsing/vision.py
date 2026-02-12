"""Claude Vision API extractor for scanned/image-based PDF tax forms."""

import base64
import io
import json
import logging
import os
import time
from pathlib import Path

from app.exceptions import VisionExtractionError
from app.parsing.detector import FormType
from app.parsing.vision_prompts import FORM_DETECTION_PROMPT, FORM_PROMPTS, SYSTEM_PROMPT

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0
MAX_IMAGE_BYTES = 4_500_000  # Stay under API's 5 MB limit


def _compress_image(pil_image, max_bytes: int = MAX_IMAGE_BYTES) -> bytes:
    """Compress a PIL image to fit within the API size limit.

    Tries JPEG at decreasing quality, then scales down if still too large.
    """
    # Try JPEG at quality 85 first (much smaller than PNG for photos/scans)
    for quality in (85, 60, 40):
        buf = io.BytesIO()
        # Convert to RGB if necessary (JPEG doesn't support alpha)
        rgb_image = pil_image.convert("RGB") if pil_image.mode != "RGB" else pil_image
        rgb_image.save(buf, format="JPEG", quality=quality)
        if buf.tell() <= max_bytes:
            return buf.getvalue()

    # Still too large — scale down
    rgb_image = pil_image.convert("RGB") if pil_image.mode != "RGB" else pil_image
    for scale in (0.75, 0.5, 0.35):
        new_size = (int(rgb_image.width * scale), int(rgb_image.height * scale))
        resized = rgb_image.resize(new_size)
        buf = io.BytesIO()
        resized.save(buf, format="JPEG", quality=60)
        if buf.tell() <= max_bytes:
            return buf.getvalue()

    # Last resort — aggressive resize
    new_size = (int(rgb_image.width * 0.25), int(rgb_image.height * 0.25))
    resized = rgb_image.resize(new_size)
    buf = io.BytesIO()
    resized.save(buf, format="JPEG", quality=40)
    return buf.getvalue()


class VisionExtractor:
    """Extracts structured data from tax form images using Claude Vision API."""

    MODEL = "claude-sonnet-4-20250514"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self._api_key:
            raise VisionExtractionError("", "ANTHROPIC_API_KEY not set. Export it or pass --vision with an API key.")
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                raise VisionExtractionError(
                    "",
                    "anthropic package not installed. Run: pip install anthropic",
                )
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def detect_form_type(self, images: list[bytes]) -> FormType | None:
        """Detect the form type from page images using Claude Vision."""
        response_text = self._call_claude_vision(
            images[:1],  # First page is enough for detection
            SYSTEM_PROMPT,
            FORM_DETECTION_PROMPT,
        )
        result = self._parse_json_response(response_text)
        if isinstance(result, dict):
            form_type_str = result.get("form_type")
            if form_type_str:
                try:
                    return FormType(form_type_str.lower())
                except ValueError:
                    return None
        return None

    def extract(self, images: list[bytes], form_type: FormType) -> dict | list[dict]:
        """Extract structured data from form images using Claude Vision.

        Args:
            images: List of PNG image bytes (one per page).
            form_type: The type of tax form being extracted.

        Returns:
            Extracted data as dict or list of dicts matching the form's schema.
        """
        prompt = FORM_PROMPTS.get(form_type)
        if not prompt:
            raise VisionExtractionError("", f"No vision prompt defined for form type: {form_type.value}")

        response_text = self._call_claude_vision(images, SYSTEM_PROMPT, prompt)
        result = self._parse_json_response(response_text)

        if result is None:
            raise VisionExtractionError("", "Claude Vision returned no parseable JSON.")

        return result

    @staticmethod
    def pdf_to_images(pdf_path: Path, resolution: int = 300) -> list[bytes]:
        """Convert PDF pages to PNG image bytes.

        Args:
            pdf_path: Path to the PDF file.
            resolution: DPI resolution for rendering (default 300).

        Returns:
            List of PNG image bytes, one per page.
        """
        try:
            import pdfplumber
        except ImportError:
            raise VisionExtractionError(
                str(pdf_path),
                "pdfplumber is not installed. Run: pip install pdfplumber",
            )

        images: list[bytes] = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                pil_image = page.to_image(resolution=resolution).original
                img_bytes = _compress_image(pil_image)
                images.append(img_bytes)
        return images

    def _call_claude_vision(self, images: list[bytes], system_prompt: str, user_prompt: str) -> str:
        """Call Claude Vision API with images and prompts, with retry logic.

        Args:
            images: PNG image bytes to send.
            system_prompt: System-level instructions.
            user_prompt: Form-specific extraction prompt.

        Returns:
            Raw text response from Claude.
        """
        content: list[dict] = []
        for img_bytes in images:
            b64_data = base64.b64encode(img_bytes).decode("utf-8")
            # Detect media type from magic bytes
            media_type = "image/jpeg" if img_bytes[:2] == b"\xff\xd8" else "image/png"
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": b64_data,
                },
            })
        content.append({"type": "text", "text": user_prompt})

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.messages.create(
                    model=self.MODEL,
                    max_tokens=4096,
                    system=system_prompt,
                    messages=[{"role": "user", "content": content}],
                )
                return response.content[0].text
            except Exception as exc:
                last_error = exc
                error_str = str(exc)
                # Retry on rate limits (429) and server errors (5xx)
                is_retryable = "429" in error_str or "500" in error_str or "502" in error_str or "503" in error_str
                if is_retryable and attempt < MAX_RETRIES - 1:
                    backoff = INITIAL_BACKOFF * (2 ** attempt)
                    logger.warning("Vision API call failed (attempt %d/%d): %s. Retrying in %.1fs...",
                                   attempt + 1, MAX_RETRIES, exc, backoff)
                    time.sleep(backoff)
                else:
                    break

        raise VisionExtractionError("", f"API call failed after {MAX_RETRIES} attempts: {last_error}")

    @staticmethod
    def _parse_json_response(response_text: str) -> dict | list | None:
        """Parse JSON from Claude's response, handling markdown fences.

        Args:
            response_text: Raw text response from Claude.

        Returns:
            Parsed JSON as dict or list, or None if parsing fails.
        """
        text = response_text.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            # Remove opening fence (with optional language tag)
            first_newline = text.index("\n") if "\n" in text else len(text)
            text = text[first_newline + 1:]
            # Remove closing fence
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3].rstrip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to find JSON object or array in the response
            for start_char, end_char in [("{", "}"), ("[", "]")]:
                start = text.find(start_char)
                end = text.rfind(end_char)
                if start != -1 and end != -1 and end > start:
                    try:
                        return json.loads(text[start:end + 1])
                    except json.JSONDecodeError:
                        continue
            return None
