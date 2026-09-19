# ============================================================================
# jarvis_notify.py — Jarvis v4.1 notifications + structured clipboard
#
#   • toast_notify_tool       — Windows toast notification (winotify,
#                               PowerShell fallback)
#   • clipboard_history_tool  — structured clipboard memory: save/load/list
#                               named clips (in-memory + Documents file)
#
# Conventions: async, never raises, Urdu/English results, action logging.
# ============================================================================
import asyncio
import datetime
import json
import logging
import os

from livekit.agents import function_tool

import jarvis_safety as safety

logger = logging.getLogger(__name__)

try:  # console-safe output (cp1252 consoles choke on Urdu / emoji)
    import sys as _sys

    # getattr: the stubs type stdout as TextIO, which has no `reconfigure`
    # (it exists only on the real TextIOWrapper) — keeps type-checkers clean.
    _reconfigure = getattr(_sys.stdout, "reconfigure", None)
    if callable(_reconfigure):
        _reconfigure(encoding="utf-8")
except Exception:
    pass

_CLIP_STORE = os.path.join(os.path.expanduser("~"), "Documents", "Jarvis_Clips.json")
_clips = {}  # name -> {"text", "saved"}


def _load_clips():
    global _clips
    if _clips:
        return
    try:
        with open(_CLIP_STORE, "r", encoding="utf-8") as f:
            _clips = json.load(f)
    except Exception:
        _clips = {}


def _save_clips():
    try:
        os.makedirs(os.path.dirname(_CLIP_STORE), exist_ok=True)
        with open(_CLIP_STORE, "w", encoding="utf-8") as f:
            json.dump(_clips, f, ensure_ascii=False, indent=1)
    except Exception as e:
        logger.warning(f"clip save failed: {e}")

# ──────────────────────────────────────────────
# Toast notifications
# ──────────────────────────────────────────────
@function_tool
async def toast_notify_tool(title: str, message: str = "",
                            duration: str = "short") -> str:
    """Windows toast notification دکھائیں (screen کے corner پر)۔ title + optional
    message۔ duration: 'short' یا 'long'۔"""
    title = (title or "").strip() or "Jarvis"
    message = (message or "").strip()
    duration = "long" if (duration or "").strip().lower() == "long" else "short"

    def _toast():
        try:
            from winotify import Notification, audio
            # winotify takes `duration` in the constructor — there is no
            # set_duration() method (that call only ever raised, silently
            # pushing every "long" toast onto the PowerShell fallback path).
            n = Notification(app_id="Jarvis 4.0", title=title,
                             msg=message or " ", duration=duration)
            try:
                n.set_audio(audio.Silent, loop=False)
            except Exception:
                pass
            n.show()
            return True
        except Exception:
            try:
                import subprocess
                ps = (
                    "[Windows.UI.Notifications.ToastNotificationManager, "
                    "Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
                    "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent"
                    "([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
                    "$x=[xml]$t.GetXml(); "
                    "$x.GetElementsByTagName('text')[0].AppendChild("
                    "$x.CreateTextNode('" + title + "')) | Out-Null; "
                    "$x.GetElementsByTagName('text')[1].AppendChild("
                    "$x.CreateTextNode('" + (message or " ") + "')) | Out-Null; "
                    "$t.LoadXml($x.OuterXml); "
                    "$n=[Windows.UI.Notifications.ToastNotification]::new($t); "
                    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier"
                    "('Jarvis 4.0').Show($n)"
                )
                subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                                "-Command", ps], timeout=10,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except Exception:
                return False
    ok = await asyncio.to_thread(_toast)
    safety.record_action("toast", f"{title}: {message[:60]}", ok)
    return (f"🔔 Notification دکھا دیا: '{title}'" if ok
            else "❌ Notification نہیں دکھ سکا (Focus assist on تو نہیں؟)")

# ──────────────────────────────────────────────
# Structured clipboard (named clips)
# ──────────────────────────────────────────────
@function_tool
async def clipboard_history_tool(action: str, name: str = "", text: str = "") -> str:
    """Structured clipboard memory — named clips: action='save' (ابھی کے کلپ بورڈ
    کا content یا دیا گیا text نام سے محفوظ)، 'load' (کلپ بورڈ پر واپس)،
    'list' (محفوظ clips)، 'delete'۔ مثال: save name='email_template'۔
    یہ Windows کے Win+V history سے الگ — Jarvis کی اپنی یادداشت۔"""
    action = (action or "").strip().lower()
    _load_clips()
    if action == "save":
        if not name:
            return "❌ clip کا نام دیں (جیسے 'password_note')۔"
        content = (text or "").strip()
        if not content:
            import pyperclip
            content = await asyncio.to_thread(pyperclip.paste)
            content = (content or "").strip()
        if not content:
            return "❌ محفوظ کرنے کے لیے text دیں یا کلپ بورڈ پر کچھ رکھیں۔"
        _clips[name] = {"text": content[:20000],
                        "saved": datetime.datetime.now().isoformat(timespec="minutes")}
        _save_clips()
        safety.record_action("clip_save", name, True)
        return f"💾 Clip '{name}' محفوظ ({len(content)} chars)۔"
    if action == "load":
        clip = _clips.get(name)
        if not clip:
            return f"❌ Clip '{name}' نہیں ملا — 'list' action سے فہرست دیکھیں۔"
        import pyperclip
        await asyncio.to_thread(pyperclip.copy, clip["text"])
        return (f"📋 Clip '{name}' کلپ بورڈ پر ({len(clip['text'])} chars) — "
                "Ctrl+V سے paste کریں۔")
    if action == "list":
        if not _clips:
            return "(کوئی محفوظ clip نہیں — 'save' سے بنائیں)"
        lines = [f"• {n}  ({len(c['text'])} chars, {c.get('saved', '?')})"
                 for n, c in sorted(_clips.items())]
        return "💾 محفوظ clips:\n" + "\n".join(lines[:40])
    if action == "delete":
        if name in _clips:
            _clips.pop(name)
            _save_clips()
            safety.record_action("clip_delete", name, True)
            return f"🗑️ Clip '{name}' حذف۔"
        return f"❌ Clip '{name}' نہیں ملا۔"
    return "❌ action: save / load / list / delete"
