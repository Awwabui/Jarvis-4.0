# ============================================================================
# vision/ocr.py — optional OCR layer (text the UIA tree can't see)
#
#   ocr_screen() → read all visible text off the screen via Tesseract
#                  (pytesseract). Gracefully reports when Tesseract is not
#                  installed — Jarvis keeps working without OCR.
#
# Install the engine once (optional):
#   winget install UB-Mannheim.TesseractOCR
#   (or set TESSERACT_CMD in .env to tesseract.exe path)
# ============================================================================
import os


def is_ocr_available() -> bool:
    """True when pytesseract + the Tesseract binary are both usable."""
    try:
        import pytesseract
        cmd = (os.getenv("TESSERACT_CMD") or "").strip()
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def ocr_screen(question: str = "") -> str:
    """Capture the screen and OCR every visible text block.

    Returns a plain-text report. Raises only when OCR is unavailable —
    callers (vision/tools.py) turn that into a friendly message.
    """
    from PIL import Image
    import pytesseract

    cmd = (os.getenv("TESSERACT_CMD") or "").strip()
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd

    from vision import screen_capture
    img = screen_capture.capture_screen()
    if img is None:
        raise RuntimeError("Screen capture failed.")

    # 2x upscale + grayscale improves small-text recognition notably
    try:
        img = img.resize((img.width * 2, img.height * 2), Image.Resampling.LANCZOS)
        img = img.convert("L")
    except Exception:
        pass

    text = pytesseract.image_to_string(img, lang="eng")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    header = ("🔍 OCR (سکرین کا پڑھا ہوا متن" +
              (f" — سوال: {question}" if question else "") + "):\n")
    if not lines:
        return header + "(کوئی قابلِ پڑھائی متن نظر نہیں آیا)"
    return header + "\n".join(lines[:120])
