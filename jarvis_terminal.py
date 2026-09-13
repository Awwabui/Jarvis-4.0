# ============================================================================
# Terminal access layer — v3 (speed + power upgrade).
# Persistent working directory (cd persists across calls).
# Destructive commands are blocked; PowerShell runs via EncodedCommand
# (robust quoting — scripts with quotes/braces never break).
# ============================================================================
import asyncio
import base64
import logging
import os
import re

from livekit.agents import function_tool

logger = logging.getLogger(__name__)

_CWD = os.getcwd()

# Destructive / irreversible commands — blocked automatically.
_BLOCKED = [
    (r"\bformat\s+[a-z]\s*:", "ڈرائیو فارمیٹ"),
    (r"\bformat-volume\b", "والیوم فارمیٹ"),
    (r"\b(?:rd|rmdir)\b[^\n]*/s", "ریکرسو فولڈر ڈیلیٹ"),
    (r"\bdel\b[^\n]*/[^\s]*s", "ریکرسو فائل ڈیلیٹ"),
    (r"\bremove-item\b[^\n]*-recurse", "ریکرسو ڈیلیٹ"),
    (r"\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)\b", "ریکرسو فائل ڈیلیٹ"),
    (r"\brm\s+-rf\s+/|--no-preserve-root", "سسٹم فائل ڈیلیٹ"),
    (r"\bdiskpart\b", "ڈسک پارٹیشننگ"),
    (r"\bshutdown\b", "سسٹم بند/ری اسٹارٹ"),
    (r"\bcipher\s*/w\b", "سیکیور ڈسک وائپ"),
    (r"\breg\s+(?:delete|add)\b", "رجسٹری تبدیلی"),
    (r"\bdd\s+if=", "را ڈسک رائٹ"),
    (r"\bmkfs(?:\.\w+)?\b", "فائل سسٹم فارمیٹ"),
    (r"\bbcde?dit\b|\bsfc\b|\bdism\b", "سسٹم بوٹ/امیج تبدیلی"),
    (r"\bnet\s+(?:user|localgroup)\b", "یوزر اکاؤنٹ تبدیلی"),
    (r"\btakeown\b|\bicacls\b|\bcacls\b", "اجازتوں کی تبدیلی"),
]


def _blocked_reason(command: str):
    for pattern, reason in _BLOCKED:
        if re.search(pattern, command, re.IGNORECASE):
            return reason
    return None


def _smart_trim(text: str, max_chars: int = 6000) -> str:
    """Keep first 4500 + last 1500 chars with a separator when trimming."""
    if len(text) <= max_chars:
        return text
    head = text[:4500]
    tail = text[-1500:]
    return f"{head}\n\n… [آؤٹ پٹ بہت لمبی تھی، درمیانی حصہ حذف کیا گیا] …\n\n{tail}"


def _decode_output(raw: bytes) -> str:
    """Try UTF-8 first, then common Windows code pages."""
    for enc in ("utf-8", "cp1252", "cp936", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")


def _clean_ps(text: str) -> str:
    """Clean PowerShell EncodedCommand output.

    With stderr merged, PowerShell may serialize streams as CLIXML:
        #< CLIXML\\r\\n
        <plain output>\\r\\n
        <Objs …><Obj S="progress">…</Obj><S S="Error">message</S>…</Objs>

    Keeps: plain output + real error messages (first lines only).
    Drops: the serialization envelope + progress-record noise.
    """
    text = text or ""
    if "#< CLIXML" in text:
        text = text.replace("#< CLIXML", "")

    if "<Objs" in text:
        idx = text.find("<Objs")
        end = text.find("</Objs>")
        if end != -1:
            envelope = text[idx:end + len("</Objs>")]
            tail = text[end + len("</Objs>"):]
        else:
            envelope = text[idx:]
            tail = ""
        plain = text[:idx].strip()

        err_segs = re.findall(r'<S\s[^>]*?S="Error"[^>]*>([\s\S]*?)</S>', envelope)
        err_text = ""
        if err_segs:
            joined = "".join(err_segs)
            joined = joined.replace("_x000D__x000A_", "\n").replace("_x000D_", "")
            lines = [
                l for l in joined.splitlines()
                if l.strip() and not l.strip().startswith("+")
                and "CategoryInfo" not in l and "FullyQualifiedErrorId" not in l
            ]
            err_text = "\n".join(lines[:4]).strip()

        out = plain
        if err_text:
            out = f"{out}\n{err_text}" if out else err_text
        if tail.strip():
            out = f"{out}\n{tail.strip()}" if out else tail.strip()
        return out.strip()

    return text.replace("_x000D__x000A_", "\n").replace("_x000D_", "").strip()
@function_tool
async def terminal_tool(command: str, timeout: int = 15) -> str:
    """Execute a shell command in the persistent working directory and return
    output + exit code. `cd <dir>` updates the persistent working directory."""
    global _CWD

    # Handle cd locally so directory persists across calls.
    m = re.match(r"\s*cd\s+(.+)$", command, re.IGNORECASE)
    if m:
        target = m.group(1).strip().strip('"').strip("'")
        new = target if os.path.isabs(target) else os.path.abspath(os.path.join(_CWD, target))
        if os.path.isdir(new):
            _CWD = new
            return f"📁 ڈائریکٹری تبدیل ہو گئی:\n{_CWD}"
        return f"❌ ڈائریکٹری نہیں ملی: {new}"

    reason = _blocked_reason(command)
    if reason:
        logger.warning(f"⛔ بلاک: {command} ({reason})")
        return (
            f"⛔ یہ کمانڈ ({reason}) خودکار نہیں چل سکتی — نقصان دہ ہو سکتی ہے۔\n"
            "اگر ضروری ہو تو براہ کرم خود terminal میں چلائیں۔"
        )

    logger.info(f"Terminal: {command!r}  cwd={_CWD}")
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=_CWD,
        )
    except Exception as e:
        return f"❌ کمانڈ شروع نہیں ہو سکی: {e}"

    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return f"⏱️ کمانڈ {timeout}s میں مکمل نہیں ہوئی: {command}"

    text = _decode_output(out).strip() if out else "(کوئی آؤٹ پٹ نہیں)"
    text = _smart_trim(text)
    code = proc.returncode
    return f"خروج کوڈ: {code}\n📁 cwd: {_CWD}\n────────────\n{text}"


@function_tool
async def terminal_run_powershell(script: str, timeout: int = 15) -> str:
    """PowerShell script inline چلائیں (Windows-specific)۔
    مثال: Get-Process | Sort CPU -Descending | Select -First 5"""
    global _CWD

    reason = _blocked_reason(script)
    if reason:
        return f"⛔ یہ script ({reason}) نہیں چلا سکتا۔"

    logger.info(f"PowerShell: {script!r}")
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell", "-NoProfile", "-NonInteractive",
            "-EncodedCommand",
            base64.b64encode(script.encode("utf-16-le")).decode("ascii"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=_CWD,
        )
    except Exception as e:
        return f"❌ PowerShell شروع نہیں ہو سکا: {e}"

    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return f"⏱️ PowerShell {timeout}s میں مکمل نہیں ہوئی۔"

    text = _decode_output(out).strip() if out else "(کوئی آؤٹ پٹ نہیں)"
    text = _smart_trim(_clean_ps(text))
    return f"⚡ PowerShell نتیجہ (exit {proc.returncode}):\n{text}"


@function_tool
async def terminal_pwd() -> str:
    """موجودہ ڈائریکٹری دکھائیں۔"""
    return f"📁 موجودہ ڈائریکٹری:\n{_CWD}"
