# ============================================================================
# jarvis_reminders.py — voice reminders for Jarvis v4.0 PRO
# A reminder fires N minutes later and Jarvis SPEAKS it out loud through the
# live session (no extra deps, no polling thread).
# ============================================================================
import asyncio
import logging

from livekit.agents import function_tool, RunContext

logger = logging.getLogger(__name__)

_MAX_ACTIVE = 12
_active: set = set()


@function_tool
async def set_reminder_tool(context: RunContext, minutes: float, message: str) -> str:
    """یاد دہانی سیٹ کریں — `minutes` کے بعد Jarvis آواز میں message یاد دلائے گا۔
    مثال: minutes=30, message='Meetings کی تیاری'۔"""
    try:
        delay = max(5.0, float(minutes) * 60.0)
    except (TypeError, ValueError):
        return "❌ minutes عدد ہونا چاہیے۔"
    message = (message or "").strip()
    if not message:
        return "❌ یاد دہانی کا message دیں۔"
    if len(_active) >= _MAX_ACTIVE:
        return "⚠ بہت زیادہ یاد دہانیاں فعال ہیں — پہلے کچھ مکمل ہونے دیں۔"

    async def _fire():
        try:
            await asyncio.sleep(delay)
            session = getattr(context, "session", None)
            if session is not None:
                await session.say(f"یاد دہانی: {message}")
            logger.info(f"⏰ Reminder fired: {message}")
        except Exception as e:
            logger.warning(f"Reminder failed: {e}")
        finally:
            _active.discard(id(_fire))

    task = asyncio.get_running_loop().create_task(_fire())
    _active.add(id(_fire))
    task.add_done_callback(lambda _t: _active.discard(id(_fire)))
    human = f"{minutes:g} منٹ" if minutes < 60 else f"{minutes / 60:.1f} گھنٹے"
    return f"⏰ یاد دہانی سیٹ: '{message}' — {human} بعد آواز میں بتاؤں گا۔"
