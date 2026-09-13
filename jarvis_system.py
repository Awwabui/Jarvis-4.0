# ============================================================================
# jarvis_system.py — Jarvis v4.0 PRO system layer
#
# Pro Windows control tools:
#   • system_control_tool  — lock / sleep / restart / shutdown / brightness /
#                            recycle-bin / dns flush (destructive ones need confirm)
#   • system_info_tool     — CPU / RAM / disk / battery / uptime (psutil)
#   • media_control_tool   — play-pause / next / previous / stop (media keys)
#   • set_volume_tool      — EXACT volume percent (pycaw, no key spam)
#   • process_tool         — list top processes / kill an app by name
#   • clipboard_tool       — read / set / clear clipboard
#   • run_command_tool     — Win+R style run commands (msconfig, regedit,
#                            services.msc, ms-settings:, shell:, URLs …)
#   • save_note_tool       — append a timestamped note to Documents
# ============================================================================
import asyncio
import datetime
import logging
import os

from livekit.agents import function_tool

logger = logging.getLogger(__name__)

try:
    import sys as _sys
    _sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DOCS_NOTE_FILE = os.path.join(os.path.expanduser("~"), "Documents", "Jarvis_Notes.txt")


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
async def _ps(script: str, timeout: int = 10) -> str:
    """Run a short PowerShell snippet, return trimmed stdout."""
    try:
        proc = await asyncio.create_subprocess_shell(
            f'powershell -NoProfile -NonInteractive -Command "{script}"',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return out.decode("utf-8", errors="replace").strip() if out else ""
    except Exception as e:
        return f"(error: {e})"


# ──────────────────────────────────────────────
# System control
# ──────────────────────────────────────────────
@function_tool
async def system_control_tool(action: str, confirm: bool = False, value: int = 0) -> str:
    """Windows سسٹم کنٹرول۔ actions: lock, sleep, restart, shutdown, cancel_shutdown,
    brightness_up, brightness_down, brightness_set (value=0-100), empty_recycle_bin, flush_dns۔
    restart/shutdown/empty_recycle_bin کے لیے confirm=True ضروری ہے (پہلے user سے پوچھیں)۔"""
    action = (action or "").strip().lower()

    if action == "lock":
        os.system("rundll32.exe user32.dll,LockWorkStation")
        return "🔒 سسٹم لاک کر دیا۔"

    if action == "sleep":
        os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
        return "😴 سسٹم سلیپ میں گیا۔"

    if action == "cancel_shutdown":
        os.system("shutdown /a")
        return "↩️ طے شدہ shutdown/restart منسوخ کر دیا۔"

    if action in ("restart", "shutdown"):
        if not confirm:
            return ("⚠ یہ action سسٹم بند/ری اسٹارٹ کر دے گا۔ پہلے user سے پوچھیں، "
                    "پھر confirm=True کے ساتھ دوبارہ کال کریں۔")
        flag = "/r" if action == "restart" else "/s"
        os.system(f"shutdown {flag} /t 5")
        return (f"🔄 سسٹم {'ری اسٹارٹ' if action == 'restart' else 'شٹ ڈاؤن'} 5 سیکنڈ میں۔ "
                "منسوخ کرنا ہو تو cancel_shutdown کہیں۔")

    if action == "brightness_set":
        pct = max(0, min(100, int(value)))
        out = await _ps(
            "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)"
            f".WmiSetBrightness(1,{pct})"
        )
        if "error" in out.lower():
            return f"⚠ برائٹنس سیٹ نہیں ہو سکی (external monitor?) {out}"
        return f"💡 برائٹنس: {pct}%"

    if action in ("brightness_up", "brightness_down"):
        cur = await _ps(
            "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness).CurrentBrightness"
        )
        try:
            level = int(float(cur.strip()))
        except Exception:
            level = 50
        step = 10 if action == "brightness_up" else -10
        pct = max(0, min(100, level + step))
        await _ps(
            "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)"
            f".WmiSetBrightness(1,{pct})"
        )
        return f"💡 برائٹنس: {pct}%"

    if action == "empty_recycle_bin":
        if not confirm:
            return "⚠ Recycle Bin مکمل خالی ہوگی۔ confirm=True کے ساتھ دوبارہ کال کریں۔"
        out = await _ps("Clear-RecycleBin -Force -ErrorAction SilentlyContinue; 'done'")
        return "🗑️ Recycle Bin خالی کر دی گئی۔" if "done" in out.lower() else f"⚠ {out}"

    if action == "flush_dns":
        out = await _ps("ipconfig /flushdns")
        return f"🌐 DNS cache flush:\n{out[:300]}"

    return (f"❌ نامعلوم action: '{action}'۔ دستیاب: lock, sleep, restart, shutdown, "
            "cancel_shutdown, brightness_up, brightness_down, brightness_set, "
            "empty_recycle_bin, flush_dns")

# ──────────────────────────────────────────────
# System info
# ──────────────────────────────────────────────
@function_tool
async def system_info_tool() -> str:
    """PC کی صحت: CPU استعمال، RAM، ڈسک جگہ، بیٹری، اَپ ٹائم۔"""
    def _collect():
        import psutil
        cpu     = psutil.cpu_percent(interval=0.4)
        mem     = psutil.virtual_memory()
        disk    = psutil.disk_usage("C:\\")
        boot    = datetime.datetime.fromtimestamp(psutil.boot_time())
        up_min  = (datetime.datetime.now() - boot).total_seconds() / 60
        battery = ""
        try:
            bat = psutil.sensors_battery()
            if bat is not None:
                status = "چارجنگ ⚡" if bat.power_plugged else "بیٹری پر"
                battery = f"\n🔋 بیٹری: {bat.percent:.0f}% ({status})"
        except Exception:
            pass
        return (
            f"🖥️ PC اسٹیٹس:\n"
            f"  • CPU: {cpu:.0f}% استعمال\n"
            f"  • RAM: {mem.percent:.0f}% استعمال "
            f"({mem.used // (1024**3)}GB / {mem.total // (1024**3)}GB)\n"
            f"  • ڈسک C:: {disk.percent:.0f}% بھری "
            f"({disk.free // (1024**3)}GB خالی)\n"
            f"  • اَپ ٹائم: {int(up_min // 60)} گھنٹے {int(up_min % 60)} منٹ"
            f"{battery}"
        )
    try:
        return await asyncio.to_thread(_collect)
    except Exception as e:
        return f"❌ سسٹم info نہیں مل سکی: {e}"


# ──────────────────────────────────────────────
# Media + volume
# ──────────────────────────────────────────────
@function_tool
async def media_control_tool(action: str) -> str:
    """میڈیا کنٹرول (کوئی بھی ایپ): play_pause / next / previous / stop۔"""
    import pyautogui
    key_map = {
        "play": "playpause", "pause": "playpause", "play_pause": "playpause",
        "playpause": "playpause", "next": "nexttrack", "previous": "prevtrack",
        "prev": "prevtrack", "stop": "stop",
    }
    key = key_map.get((action or "").strip().lower())
    if not key:
        return f"❌ نامعلوم media action: '{action}' (play_pause/next/previous/stop)"
    await asyncio.to_thread(pyautogui.press, key)
    return f"🎵 media '{action}' بھیج دیا۔"


@function_tool
async def set_volume_tool(percent: int) -> str:
    """والیوم بالکل درست فیصد پر سیٹ کریں (0-100)۔ موجودہ لیول بھی بتاتا ہے۔"""
    pct = max(0, min(100, int(percent)))
    try:
        from ctypes import POINTER, cast
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        def _set():
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            vol = cast(interface, POINTER(IAudioEndpointVolume))
            old = int(round(vol.GetMasterVolumeLevelScalar() * 100))
            vol.SetMasterVolumeLevelScalar(pct / 100.0, None)
            muted = bool(vol.GetMute())
            return old, muted
        old, muted = await asyncio.to_thread(_set)
        return f"🔊 والیوم {old}% → {pct}% سیٹ{' (mute)' if muted else ''}۔"
    except ImportError:
        # Fallback: approximate with key presses
        import pyautogui
        presses = abs(pct - 50) // 2
        key = "volumeup" if pct > 50 else "volumedown"
        for _ in range(min(presses, 40)):
            await asyncio.to_thread(pyautogui.press, key)
        return f"🔊 والیوم تقریباً {pct}% (pycaw نصب نہیں — تخمینی سیٹ)۔"
    except Exception as e:
        return f"❌ والیوم سیٹ ناکام: {e}"
# ──────────────────────────────────────────────
# Processes
# ──────────────────────────────────────────────
@function_tool
async def process_tool(action: str = "list", name: str = "", confirm: bool = False) -> str:
    """چلتے پروسیسس: action='list' (RAM کے حساب سے top 15) یا
    action='kill' (ایپ بند کریں — name='chrome' جیسی; confirm=True ضروری)۔"""
    action = (action or "").strip().lower()
    if action == "list":
        import psutil
        procs = []
        for p in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                info = p.info
                mem_mb = (info["memory_info"].rss / (1024 * 1024)) if info["memory_info"] else 0
                procs.append((info["name"] or "?", info["pid"], mem_mb))
            except Exception:
                continue
        procs.sort(key=lambda x: x[2], reverse=True)
        lines = [f"• {n} (pid {pid}, {mem:.0f} MB)" for n, pid, mem in procs[:15]]
        return "⚙️ Top پروسیسس (RAM):\n" + "\n".join(lines)

    if action == "kill":
        target = (name or "").strip().lower()
        if not target:
            return "❌ kill کے لیے app کا نام دیں (جیسے 'chrome')۔"
        if not target.endswith(".exe"):
            target += ".exe"
        if not confirm:
            return (f"⚠ '{target}' کے تمام windows بند ہو جائیں گے (بغیر save)۔ "
                    "confirm=True کے ساتھ دوبارہ کال کریں۔")
        proc = await asyncio.create_subprocess_shell(
            f'taskkill /IM "{target}" /F',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        text = out.decode("utf-8", errors="replace").strip() if out else ""
        if proc.returncode == 0:
            return f"✅ {target} بند کر دیا۔"
        return f"❌ kill ناکام: {text[:300]}"
    return "❌ action: 'list' یا 'kill'"


# ──────────────────────────────────────────────
# Clipboard
# ──────────────────────────────────────────────
@function_tool
async def clipboard_tool(action: str = "get", text: str = "") -> str:
    """کلپ بورڈ: action='get' (پڑھیں), 'set' (text محفوظ کریں), 'clear'۔"""
    import pyperclip
    action = (action or "").strip().lower()
    if action == "get":
        try:
            content = await asyncio.to_thread(pyperclip.paste)
            content = (content or "").strip()
            if not content:
                return "📋 کلپ بورڈ خالی ہے۔"
            show = content[:1500] + ("…" if len(content) > 1500 else "")
            return f"📋 کلپ بورڈ:\n{show}"
        except Exception as e:
            return f"❌ کلپ بورڈ نہیں پڑھ سکا: {e}"
    if action == "set":
        if not text:
            return "❌ 'set' کے لیے text دیں۔"
        await asyncio.to_thread(pyperclip.copy, text)
        return f"📋 کلپ بورڈ پر محفوظ ({len(text)} حروف)۔"
    if action == "clear":
        await asyncio.to_thread(pyperclip.copy, "")
        return "📋 کلپ بورڈ خالی کر دیا۔"
    return "❌ action: get / set / clear"


# ──────────────────────────────────────────────
# Win+R style run commands
# ──────────────────────────────────────────────
@function_tool
async def run_command_tool(command: str) -> str:
    """Win+R طرز کی کمانڈ چلائیں: msconfig, regedit, services.msc, devmgmt.msc,
    ms-settings:*, shell:*, URLs (https://…), control panel applets وغیرہ۔
    ڈیسٹرکٹو کمانز (format, del /s …) terminal_tool خود بلاک کرتا ہے۔"""
    cmd = (command or "").strip()
    if not cmd:
        return "❌ کمانڈ خالی ہے۔"
    lowered = cmd.lower()
    if lowered.startswith(("format ", "del ", "rd ", "rmdir ")):
        return "⛔ یہ کمانڈ خودکار نہیں چل سکتی — نقصان دہ ہے۔"
    try:
        os.startfile(cmd)
        return f"🚀 چلایا: {cmd}"
    except Exception:
        pass
    try:
        proc = await asyncio.create_subprocess_shell(f'start "" "{cmd}"', shell=True)
        await asyncio.wait_for(proc.wait(), timeout=6)
        return f"🚀 چلایا (shell): {cmd}"
    except Exception as e:
        return f"❌ '{cmd}' نہیں چل سکا: {e}"


# ──────────────────────────────────────────────
# Notes
# ──────────────────────────────────────────────
@function_tool
async def save_note_tool(text: str) -> str:
    """نوٹ محفوظ کریں — Documents\\Jarvis_Notes.txt میں timestamp کے ساتھ۔"""
    note = (text or "").strip()
    if not note:
        return "❌ نوٹ خالی ہے۔"
    try:
        os.makedirs(os.path.dirname(_DOCS_NOTE_FILE), exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(_DOCS_NOTE_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {note}\n")
        return f"📝 نوٹ محفوظ: {_DOCS_NOTE_FILE}"
    except Exception as e:
        return f"❌ نوٹ محفوظ نہیں ہوا: {e}"

