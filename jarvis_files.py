# ============================================================================
# jarvis_files.py — Jarvis v4.1 advanced file operations
#
#   • file_search_tool    — recursive file/folder search by name pattern
#   • file_copy_tool      — copy file/folder
#   • file_move_tool      — move file/folder (confirm on overwrite)
#   • file_delete_tool    — delete to Recycle Bin (confirm required!)
#   • file_mkdir_tool     — create folder (incl. parents)
#   • recent_files_tool   — most recently modified files in a folder
#
# Safety: delete requires confirm=True (goes to Recycle Bin, recoverable);
# move requires confirm when it would overwrite; everything is gated by the
# emergency-stop switch and logged to the action trail.
# ============================================================================
import asyncio
import logging
import os
import shutil
import time
from datetime import datetime

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


def _expand(p: str) -> str:
    return os.path.expandvars(os.path.expanduser((p or "").strip().strip('"')))


def _fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n:.0f} B"
        n /= 1024
    return f"{n:.1f} GB"

@function_tool
async def file_search_tool(pattern: str, folder: str = "",
                           max_results: int = 20) -> str:
    """فائل/فولڈر تلاش کریں نام کے pattern سے (case-insensitive substring؛
    `*.pdf` جیسا glob بھی چلتا ہے)۔ folder خالی ہو تو user profile سے recursive
    search۔ max_results limit (default 20)۔"""
    pattern = (pattern or "").strip()
    if not pattern:
        return "❌ pattern دیں (جیسے 'invoice' یا '*.pdf')۔"
    base = _expand(folder) or os.path.expanduser("~")
    if not os.path.isdir(base):
        return f"❌ فولڈر نہیں ملا: {base}"
    max_results = max(1, min(50, int(max_results)))

    def _search():
        import fnmatch
        glob_style = any(ch in pattern for ch in "*?[")
        pl = pattern.lower()
        found = []
        try:
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if not d.startswith((".", "$"))]
                for name in files + dirs:
                    ok = fnmatch.fnmatch(name.lower(), pl) if glob_style \
                        else pl in name.lower()
                    if ok:
                        p = os.path.join(root, name)
                        try:
                            st = os.stat(p)
                            found.append((p, st.st_size, st.st_mtime))
                        except OSError:
                            found.append((p, 0, 0))
                        if len(found) >= max_results * 3:
                            break
                if len(found) >= max_results * 3:
                    break
        except Exception as e:
            logger.warning(f"file_search error: {e}")
        return found[:max_results]

    results = await asyncio.to_thread(_search)
    if not results:
        return f"❌ '{base}' میں '{pattern}' نہیں ملا۔"
    lines = []
    for p, size, mt in results:
        when = datetime.fromtimestamp(mt).strftime("%Y-%m-%d") if mt else "?"
        kind = "📁" if os.path.isdir(p) else "📄"
        lines.append(f"{kind} {p}  ({_fmt_size(size)}, {when})")
    safety.record_action("file_search", f"'{pattern}' in {base}", True)
    return f"🔍 '{pattern}' نتائج ({base}):\n" + "\n".join(lines)

@function_tool
async def file_copy_tool(source: str, destination: str) -> str:
    """فائل یا فولڈر copy کریں۔ destination میں نیا نام یا فولڈر دونوں چلتے ہیں۔
    موجودہ فائل overwrite ہو تو پہلے user سے پوچھ کر file_move_tool
    (action='copy', confirm=True) استعمال کریں۔"""
    src = _expand(source)
    dst = _expand(destination)
    if not os.path.exists(src):
        return f"❌ source نہیں ملا: {src}"
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src.rstrip("\\/")))
    if os.path.exists(dst):
        return (f"⚠ '{dst}' پہلے سے موجود ہے — overwrite کے لیے M. Awwab sir سے "
                "پوچھیں، پھر file_move_tool (action='copy', confirm=True) استعمال کریں۔")

    def _copy():
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
            shutil.copy2(src, dst)
    try:
        await asyncio.to_thread(_copy)
        safety.record_action("file_copy", f"{src} → {dst}", True)
        return f"📋 copy مکمل: {src} → {dst}"
    except Exception as e:
        safety.record_action("file_copy", f"{src} → {dst}: {e}", False)
        return f"❌ copy ناکام: {e}"


@function_tool
async def file_move_tool(source: str, destination: str,
                         action: str = "move", confirm: bool = False) -> str:
    """فائل/فولڈر move یا copy کریں۔ action='move' (default) یا 'copy'۔
    اگر destination پہلے سے موجود ہو (overwrite) تو پہلے M. Awwab sir سے پوچھیں
    اور confirm=True کے ساتھ دوبارہ کال کریں۔"""
    blocked = safety.gate("file_move_tool")
    if blocked:
        return blocked
    src = _expand(source)
    dst = _expand(destination)
    act = (action or "move").strip().lower()
    if act not in ("move", "copy"):
        return "❌ action: 'move' یا 'copy'"
    if not os.path.exists(src):
        return f"❌ source نہیں ملا: {src}"
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src.rstrip("\\/")))
    if os.path.exists(dst) and not confirm:
        return (f"⚠ '{dst}' پہلے سے موجود ہے — overwrite ہوگی۔ M. Awwab sir سے پوچھیں، "
                "پھر confirm=True کے ساتھ دوبارہ کال کریں۔")

    def _go():
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        if act == "move":
            shutil.move(src, dst)
        elif os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=confirm)
        else:
            shutil.copy2(src, dst)
    try:
        await asyncio.to_thread(_go)
        safety.record_action(f"file_{act}", f"{src} → {dst}", True)
        return f"🚚 {act} مکمل: {src} → {dst}"
    except Exception as e:
        safety.record_action(f"file_{act}", f"{src} → {dst}: {e}", False)
        return f"❌ {act} ناکام: {e}"

@function_tool
async def file_delete_tool(path: str, confirm: bool = False) -> str:
    """فائل/فولڈر delete کریں — Recycle Bin میں جائے گی (recoverable)۔
    پہلے M. Awwab sir سے صاف پوچھیں، پھر confirm=True کے ساتھ کال کریں۔
    بغیر confirm کے کبھی نہ کریں۔"""
    blocked = safety.gate("file_delete_tool", cooldown=15.0)
    if blocked:
        return blocked
    target = _expand(path)
    if not os.path.exists(target):
        return f"❌ نہیں ملا: {target}"
    if not confirm:
        return (f"⚠ DELETE کی تصدیق: '{target}' Recycle Bin میں جائے گا۔ "
                "M. Awwab sir سے پوچھیں، پھر confirm=True کے ساتھ دوبارہ کال کریں۔")

    def _del():
        from send2trash import send2trash
        send2trash(target)
    try:
        await asyncio.to_thread(_del)
        safety.record_action("file_delete", target, True)
        return f"🗑️ Recycle Bin بھیج دیا: {target}"
    except Exception as e:
        safety.record_action("file_delete", f"{target}: {e}", False)
        return f"❌ delete ناکام: {e}"


@function_tool
async def file_mkdir_tool(path: str) -> str:
    """نیا فولڈر بنائیں (بچوں سمیت — parents automatically)۔"""
    target = _expand(path)
    if not target:
        return "❌ فولڈر کا پاتھ دیں۔"

    def _mk():
        os.makedirs(target, exist_ok=True)
        return os.path.isdir(target)
    try:
        ok = await asyncio.to_thread(_mk)
        if ok:
            safety.record_action("file_mkdir", target, True)
            return f"📁 فولڈر تیار: {target}"
        return f"❌ فولڈر نہیں بن سکا: {target}"
    except Exception as e:
        return f"❌ mkdir ناکام: {e}"


@function_tool
async def recent_files_tool(folder: str = "Downloads", limit: int = 10) -> str:
    """کسی فولڈر کی حالیہ فائلیں (modified time کے حساب سے)۔ folder: نام جیسے
    Downloads/Desktop/Documents (user profile میں) یا مکمل پاتھ۔
    'recent files دکھاؤ' جیسے کام کے لیے۔"""
    limit = max(1, min(30, int(limit)))
    known = {"downloads": os.path.expanduser("~/Downloads"),
             "desktop": os.path.expanduser("~/Desktop"),
             "documents": os.path.expanduser("~/Documents"),
             "pictures": os.path.expanduser("~/Pictures"),
             "music": os.path.expanduser("~/Music"),
             "videos": os.path.expanduser("~/Videos")}
    key = (folder or "downloads").strip().lower()
    if os.path.isabs(_expand(folder)) and os.path.isdir(_expand(folder)):
        resolved = _expand(folder)
    else:
        resolved = known.get(key, os.path.join(os.path.expanduser("~"), folder))
    if not os.path.isdir(resolved):
        return f"❌ فولڈر نہیں ملا: {resolved}"

    def _scan():
        items = []
        for name in os.listdir(resolved):
            p = os.path.join(resolved, name)
            try:
                st = os.stat(p)
                items.append((st.st_mtime, p, st.st_size))
            except OSError:
                continue
        items.sort(reverse=True)
        return items[:limit]
    items = await asyncio.to_thread(_scan)
    if not items:
        return f"📁 '{resolved}' خالی ہے۔"
    now = time.time()
    lines = []
    for mt, p, size in items:
        age = now - mt
        when = ("ابھی" if age < 3600 else
                f"{int(age // 3600)} گھنٹے پہلے" if age < 86400 else
                datetime.fromtimestamp(mt).strftime("%Y-%m-%d"))
        lines.append(f"📄 {os.path.basename(p)}  ({_fmt_size(size)}, {when})")
    return f"🕒 حالیہ فائلیں ({resolved}):\n" + "\n".join(lines)
