# ============================================================================
# vision/vision_agent.py — REAL vision analysis via google-genai
#
# Reuses the EXISTING Jarvis AI stack (GOOGLE_API_KEY + google-genai) — it does
# NOT create a second AI system. The realtime voice LLM (gemini-live-*) is
# audio-only, so analysis uses the same provider's standard multimodal model,
# configurable via VISION_MODEL in .env (default: gemini-2.5-flash,
# vision-capable).
#
# Privacy: images are transmitted ONLY to the configured vision provider
# (Google), never saved to disk by this module.
# ============================================================================
import os
from concurrent.futures import ThreadPoolExecutor

from vision import vision_prompts

# NOTE: google.genai is imported lazily inside functions (import cost ~1-2s;
# keeping it lazy lets the watcher thread start instantly at startup).

GOOGLE_API_KEY = "GOOGLE_API_KEY"
VISION_MODEL = "gemini-3.6-flash"
VISION_TIMEOUT = 25.0  # seconds

_vision_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="jarvis-vision")


class VisionError(Exception):
    """Vision analysis failed (API error, network error, model error)."""


class VisionUnavailable(Exception):
    """Vision is not configured/usable (e.g. GOOGLE_API_KEY missing)."""


def is_vision_configured() -> bool:
    """True when a Google API key is present (modular VISION_MODEL default)."""
    return bool((os.getenv("GOOGLE_API_KEY") or "").strip())


def _get_client():
    """Build a google-genai Client from the existing GOOGLE_API_KEY env."""
    # `from google.genai import Client` — the `from google import genai` form is
    # not resolvable by type-checkers (google is a namespace package).
    from google.genai import Client as GenaiClient

    api_key = (os.getenv("GOOGLE_API_KEY") or "").strip()
    if not api_key:
        raise VisionUnavailable("GOOGLE_API_KEY is not configured.")
    try:
        return GenaiClient(api_key=api_key)
    except Exception as e:
        raise VisionUnavailable(f"Could not initialize vision client: {e}") from e


def _call_model(client, model: str, contents: list) -> str:
    """Blocking generate_content call with timeout + clean error mapping."""
    from google.genai import types as genai_types

    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=genai_types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=1024,
        ),
    )
    text = (getattr(response, "text", None) or "").strip()
    if not text:
        reason = ""
        try:
            reason = str(response.candidates[0].finish_reason)
        except Exception:
            pass
        raise VisionError(f"Vision model returned no text{f' ({reason})' if reason else ''}.")
    return text


def analyze_image(png_bytes: bytes, question: str, model: str = "") -> str:
    """Analyze a screenshot (PNG bytes) with the vision model. Blocking.

    Raises VisionUnavailable / VisionError — callers handle both gracefully.
    """
    from google.genai import types as genai_types

    if not (question or "").strip():
        question = vision_prompts.QUICK_PROMPT

    model = (model or (os.getenv("VISION_MODEL") or "").strip() or VISION_MODEL)
    if not is_vision_configured():
        raise VisionUnavailable("GOOGLE_API_KEY is not configured.")

    client = _get_client()
    contents = [
        genai_types.Part.from_bytes(data=png_bytes, mime_type="image/png"),
        vision_prompts.ANALYSIS_PROMPT.format(question=question),
    ]
    try:
        future = _vision_executor.submit(_call_model, client, model, contents)
        return future.result(timeout=VISION_TIMEOUT)
    except VisionError:
        raise
    except Exception as e:
        msg = str(e)
        if "API key" in msg or "API_KEY" in msg or "401" in msg or "403" in msg:
            raise VisionUnavailable(f"Vision provider rejected credentials: {msg}")
        raise VisionError(f"Vision analysis failed: {msg}") from e


def describe_screen(png_bytes: bytes, question: str = "") -> str:
    """Full blocking analysis of a PNG screenshot."""
    return analyze_image(png_bytes, question)


def describe_screen_async(png_bytes: bytes, question: str, callback):
    """Fire-and-forget analysis; callback(result_or_error: str) on the worker
    thread. Used by the background watcher so it never blocks capture."""
    def _run():
        try:
            callback(analyze_image(png_bytes, question))
        except Exception as e:
            callback(e)
    _vision_executor.submit(_run)