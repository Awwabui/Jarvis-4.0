# ============================================================================
# File playback layer.
# Finds a file (MP4, MP3, PDF, PPT, images, ...) by fuzzy name across the
# common user folders + all drives and opens it with the default player.
#
# v2: uses the shared cached index from Jarvis_window_CTRL (rebuilt at most
# once per 60s, scanned in a worker thread) instead of re-walking the whole
# D:\ drive on EVERY call — which used to freeze voice interaction.
# ============================================================================
import os
import subprocess
import sys
import logging

from livekit.agents import function_tool

try:
    from Jarvis_window_CTRL import smart_search, focus_window
except ImportError:
    smart_search = None
    focus_window = None

sys.stdout.reconfigure(encoding='utf-8')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@function_tool
async def Play_file(name: str) -> str:
    """کسی فائل (MP4, MP3, PDF, PPT, تصویر وغیرہ) کو نام سے تلاش کر کے چلائیں۔"""
    query = name.strip()
    if not query:
        return "❌ فائل کا نام نہیں دیا گیا۔"
    if smart_search is None:
        return "❌ سرچ ماڈیول دستیاب نہیں۔"

    item = await smart_search(query)  # cached + runs off the event loop
    if not item:
        logger.warning(f"❌ فائل نہیں ملی: {query}")
        return f"❌ '{query}' نام کی کوئی فائل نہیں ملی۔"

    try:
        logger.info(f"📂 فائل کھولی جا رہی ہے: {item['path']}")
        if os.name == 'nt':
            os.startfile(item["path"])
        else:
            subprocess.call(['open' if sys.platform == 'darwin' else 'xdg-open', item["path"]])
    except Exception as e:
        logger.error(f"❌ فائل کھولنے میں خرابی آگئی: {e}")
        return f"❌ فائل نہیں کھل سکی: {e}"

    if focus_window is not None:
        try:
            await focus_window(os.path.basename(item["path"]))
        except Exception:
            pass
    label = "فولڈر" if item["type"] == "folder" else "فائل"
    return f"✅ {label} کھول دی گئی: {item['name']}"
