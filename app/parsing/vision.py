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


def _salvage_truncated_json_array(text: str) -> list | None:
    """Attempt to recover complete JSON objects from a truncated array.

    When the API response is cut off mid-stream, the JSON array may be
    incomplete (e.g., "[{...}, {..." with no closing bracket).
    This function finds the last complete object and closes the array.
    """
    # Find each complete top-level object in the array
    depth = 0
    last_complete_end = -1
    in_string = False
    escape_next = False

    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\":
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                last_complete_end = i

    if last_complete_end <= 0:
        return None

    # Build a valid JSON array from complete objects only
    salvaged = text[:last_complete_end + 1].rstrip().rstrip(",") + "]"
    try:
        result = json.loads(salvaged)
        if isinstance(result, list) and len(result) > 0:
            return result
    except json.JSONDecodeError:
        pass
    return None


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
        """Detect the form type from page images using Claude Vision.

        Sends up to the first 3 pages to handle composite brokerage statements
        where page 1 is a summary/cover page.
        """
        # Send up to 3 pages for detection — composite docs (Robinhood, Morgan Stanley)
        # have summary cover pages that don't clearly identify a single form type.
        pages_to_check = images[:min(3, len(images))]
        response_text = self._call_claude_vision(
            pages_to_check,
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

        # Scale max_tokens based on page count — multi-page 1099-Bs need more room
        max_tokens = max(4096, len(images) * 2048)

        response_text = self._call_claude_vision(images, SYSTEM_PROMPT, prompt, max_tokens=max_tokens)
        result = self._parse_json_response(response_text)

        if result is None:
            # Log the raw response so we can diagnose extraction failures
            logger.error("Vision extraction returned no parseable JSON. Raw response (first 2000 chars):\n%s",
                         response_text[:2000])
            raise VisionExtractionError(
                "",
                f"Claude Vision returned no parseable JSON. Response preview: {response_text[:300]}",
            )

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

    def _call_claude_vision(
        self,
        images: list[bytes],
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
    ) -> str:
        """Call Claude Vision API with images and prompts, with retry logic.

        Args:
            images: PNG image bytes to send.
            system_prompt: System-level instructions.
            user_prompt: Form-specific extraction prompt.
            max_tokens: Maximum tokens in the response.

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

        # Use streaming for large requests (many pages) to avoid the 10-minute
        # non-streaming timeout limit imposed by the Anthropic API.
        use_streaming = len(images) > 5
        logger.info("Calling Claude Vision with %d page(s), max_tokens=%d, streaming=%s",
                     len(images), max_tokens, use_streaming)

        msg_params = {
            "model": self.MODEL,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": content}],
        }

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                if use_streaming:
                    response_text, stop_reason = self._stream_response(msg_params)
                else:
                    response = self.client.messages.create(**msg_params)
                    response_text = response.content[0].text
                    stop_reason = response.stop_reason

                # Check if response was truncated due to max_tokens
                if stop_reason == "max_tokens":
                    logger.warning("Vision API response was truncated (hit max_tokens=%d). "
                                   "Output may be incomplete.", max_tokens)
                return response_text
            except Exception as exc:
                last_error = exc
                error_str = str(exc)

                # If we hit the streaming requirement, retry with streaming
                if "streaming is required" in error_str.lower() and not use_streaming:
                    logger.info("Server requires streaming — retrying with streaming enabled")
                    use_streaming = True
                    continue

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

    def _stream_response(self, msg_params: dict) -> tuple[str, str]:
        """Stream a response from the API and collect the full text.

        Returns:
            Tuple of (response_text, stop_reason).
        """
        with self.client.messages.stream(**msg_params) as stream:
            response = stream.get_final_message()
        return response.content[0].text, response.stop_reason

    @staticmethod
    def _parse_json_response(response_text: str) -> dict | list | None:
        """Parse JSON from Claude's response, handling markdown fences and truncation.

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
            pass

        # Try to find JSON object or array in the response
        for start_char, end_char in [("{", "}"), ("[", "]")]:
            start = text.find(start_char)
            end = text.rfind(end_char)
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    continue

        # Handle truncated JSON arrays — response cut off mid-stream
        # Find the start of a JSON array and try to salvage complete objects
        array_start = text.find("[")
        if array_start != -1:
            truncated = text[array_start:]
            result = _salvage_truncated_json_array(truncated)
            if result is not None:
                logger.warning("Salvaged %d complete records from truncated JSON response", len(result))
                return result

        return None
