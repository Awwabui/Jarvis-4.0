# ============================================================================
# jarvis_safety.py — Jarvis v4.1 SAFETY layer (emergency stop + confirmations)
#
#   • EMERGENCY STOP   — "Jarvis stop everything" trips a global kill switch.
#                        While tripped, EVERY gated tool refuses to act.
#                        "Jarvis continue" clears it.
#   • CONFIRM TOKENS   — Destructive tools issue a short-lived token; the
#                         action only runs when the SAME token comes back with
#                         the user's explicit confirmation.
#   • COOLDOWNS        — Dangerous tools can't be spammed (min gap between calls).
#   • ACTION LOG       — Every gated call is logged to Documents\Jarvis_Actions.log
#                         and kept in a short in-memory trail for the planner.
#
# All tools here follow the existing conventions: async, @function_tool,
# Urdu/English result strings, never raise.
# ============================================================================
import datetime
import logging
import os
import time
from collections import deque

from livekit.agents import function_tool

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

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────
_TOKEN_TTL = 120.0        # confirmation token valid for 2 minutes
_DEFAULT_COOLDOWN = 10.0  # seconds between two dangerous calls of same tool
_ACTION_LOG = os.path.join(os.path.expanduser("~"), "Documents", "Jarvis_Actions.log")
_TRAIL_MAX = 30           # in-memory action trail size (planner uses this)

# ──────────────────────────────────────────────
# Global state
# ──────────────────────────────────────────────
_stopped = False          # emergency kill switch
_stopped_at = 0.0
_tokens = {}              # token -> {"action": str, "expires": float}
_last_call = {}           # tool name -> last call time (cooldowns)
_trail = deque(maxlen=_TRAIL_MAX)

# ──────────────────────────────────────────────
# Internal helpers (used by OTHER tool modules)
# ──────────────────────────────────────────────
def is_stopped() -> bool:
    """True while the emergency kill switch is tripped."""
    return _stopped


def request_stop() -> str:
    """Trip the emergency kill switch. All gated tools stop immediately."""
    global _stopped, _stopped_at
    _stopped = True
    _stopped_at = time.time()
    _tokens.clear()  # outstanding confirmations are void too
    logger.warning("EMERGENCY STOP requested")
    return "🛑 EMERGENCY STOP — تمام actions روک دیے گئے۔"


def request_continue() -> str:
    """Clear the kill switch (only the user may do this)."""
    global _stopped
    if not _stopped:
        return "کوئی emergency stop فعال نہیں تھا — Jarvis پہلے ہی جاری ہے۔"
    _stopped = False
    logger.info("Emergency stop cleared — Jarvis resumed")
    return "▶️ Emergency stop ہٹا دیا گیا — Jarvis دوبارہ جاری ہے۔"


def gate(tool_name: str, cooldown=None) -> str:
    """Central gate — call at the TOP of every dangerous/action tool.

    Returns '' when the tool may proceed, otherwise an Urdu/English
    refusal message the agent should surface to the user.
    Handles: emergency stop, per-tool cooldown.
    """
    if _stopped:
        secs = int(time.time() - _stopped_at)
        return (f"🛑 EMERGENCY STOP فعال ہے ({secs}s پہلے)۔ '{tool_name}' روک دیا گیا۔ "
                "جاری رکھنے کے لیے M. Awwab sir کو 'Jarvis continue' کہنا ہوگا۔")
    gap = _DEFAULT_COOLDOWN if cooldown is None else cooldown
    if gap > 0:
        last = _last_call.get(tool_name, 0.0)
        wait = gap - (time.time() - last)
        if wait > 0:
            return (f"⏳ '{tool_name}' کا cooldown جاری ہے — {wait:.0f}s انتظار کریں "
                    "(خطرناک ٹولز پر rate limit ہے)۔")
    _last_call[tool_name] = time.time()
    return ""


def new_token(action: str) -> str:
    """Issue a short-lived confirmation token for a destructive action."""
    token = f"C{int(time.time() * 1000) % 10**8:08d}"
    _tokens[token] = {"action": action, "expires": time.time() + _TOKEN_TTL}
    for t in [k for k, v in _tokens.items() if v["expires"] < time.time()]:
        _tokens.pop(t, None)
    return token


def check_token(token: str, action: str) -> str:
    """Return '' if token+action pair is valid & fresh, else refusal message."""
    if _stopped:
        return (f"🛑 EMERGENCY STOP فعال ہے — '{action}' نہیں ہو سکتا۔ "
                "'Jarvis continue' کے بعد دوبارہ کوشش کریں۔")
    info = _tokens.get(token)
    if not info:
        return (f"⚠ '{action}' کے لیے پہلے confirmation token لیں "
                f"(request_confirm_tool), پھر user کی تصدیق کے ساتھ "
                "confirm_action_tool چلائیں۔")
    if info["expires"] < time.time():
        _tokens.pop(token, None)
        return f"⚠ token منقضی ہو گیا — '{action}' کے لیے نیا token لیں۔"
    if info["action"] != action:
        return (f"⚠ token اس action کے لیے تھا: '{info['action']}' — "
                f"'{action}' نہیں۔ درست token استعمال کریں۔")
    _tokens.pop(token, None)  # single-use
    return ""

def record_action(tool: str, detail: str, success: bool = True) -> None:
    """Log an action (file + in-memory trail). Never raises."""
    entry = {
        "time": datetime.datetime.now().strftime("%H:%M:%S"),
        "tool": tool,
        "detail": detail[:200],
        "ok": success,
    }
    _trail.append(entry)
    try:
        os.makedirs(os.path.dirname(_ACTION_LOG), exist_ok=True)
        with open(_ACTION_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] "
                    f"{'OK ' if success else 'FAIL '} {tool}: {detail[:300]}\n")
    except Exception:
        pass


def action_trail(limit: int = 10) -> list:
    """Recent action entries (planner / action memory uses this)."""
    return list(_trail)[-max(1, limit):]


def last_failures(limit: int = 5) -> list:
    """Recent FAILED actions — planner must not repeat these blindly."""
    fails = [e for e in _trail if not e["ok"]]
    return fails[-max(1, limit):]


def _format_trail(entries) -> str:
    if not entries:
        return "(ابھی کوئی action ریکارڈ نہیں ہوا)"
    lines = []
    for e in entries:
        mark = "✅" if e["ok"] else "❌"
        lines.append(f"{mark} [{e['time']}] {e['tool']}: {e['detail']}")
    return "\n".join(lines)


# ──────────────────────────────────────────────
# Tools — emergency stop / continue
# ──────────────────────────────────────────────
@function_tool
async def emergency_stop_tool() -> str:
    """EMERGENCY STOP — "Jarvis stop everything" / "emergency stop" کہنے پر یہ
    ٹول فوراً کال کریں۔ سارے actions (clicks, typing, process kill, file ops,
    system control) فوراً روک دیے جاتے ہیں جب تک user خود 'Jarvis continue'
    نہ کہے۔ صرف user کی صراحت سے استعمال کریں۔"""
    result = request_stop()
    record_action("emergency_stop", "kill switch tripped", True)
    return result


@function_tool
async def jarvis_continue_tool() -> str:
    """'Jarvis continue' / 'jarvis resume' کہنے پر emergency stop ہٹائیں اور
    دوبارہ کام جاری کریں۔ صرف M. Awwab sir کی صراحت سے (وہ خود کہے تب)۔"""
    return request_continue()


# ──────────────────────────────────────────────
# Tools — confirmation tokens
# ──────────────────────────────────────────────
@function_tool
async def request_confirm_tool(action: str) -> str:
    """Destructive action (delete, shutdown, process kill, registry write,
    empty recycle bin, service stop…) سے پہلے user سے صاف پوچھیں اور یہ ٹول
    کال کریں۔ یہ ایک confirmation token دیتا ہے جو user کی 'ہاں' کے بعد
    confirm_action_tool میں استعمال ہوتا ہے۔ action میں مختصر وضاحت دیں۔"""
    action = (action or "").strip()
    if not action:
        return "❌ action کی مختصر وضاحت دیں (جیسے: 'kill chrome.exe')۔"
    token = new_token(action)
    return (f"⚠ CONFIRMATION درکار: '{action}'\n"
            f"M. Awwab sir سے صاف پوچھیں۔ 'ہاں' ملو تو confirm_action_tool کریں:\n"
            f"  token={token}\n"
            f"(token {int(_TOKEN_TTL / 60)} منٹ تک درست، single-use)")


@function_tool
async def confirm_action_tool(token: str, action: str) -> str:
    """User کی صراحت کے بعد destructive action finalize کریں۔ token وہی ہے
    جو request_confirm_tool نے دیا تھا، action وہی وضاحت۔ کامیابی پر token
    single-use ہو جاتا ہے۔ یہ token validate کرنے کے لیے ہے۔"""
    token = (token or "").strip()
    action = (action or "").strip()
    result = check_token(token, action)
    if result:
        return result
    return f"✅ تصدیق ہو گئی: '{action}' — اب اصل action ٹول چلائیں۔"


# ──────────────────────────────────────────────
# Tools — action memory (planner aid)
# ──────────────────────────────────────────────
@function_tool
async def recent_actions_tool(limit: int = 10) -> str:
    """آخری actions کی فہرست (✅/❌) — action memory۔ کسی step کو دہرانے سے
    پہلے چیک کریں کہ وہی action حال ہی میں fail تو نہیں ہوا۔ Failed actions
    کو اسی طریقے سے دہرانے کے بجے مختلف طریقہ آزمائیں۔"""
    limit = max(1, min(30, int(limit)))
    out = _format_trail(action_trail(limit))
    return f"📜 آخری actions:\n{out}"


@function_tool
async def failed_actions_tool() -> str:
    """حال ہی میں FAIL ہونے والے actions — planner کو نئی کوشش سے پہلے یہ
    دیکھنا چاہیے تاکہ وہی غلطی دہرائی نہ جائے۔"""
    out = _format_trail(last_failures(5))
    return f"❌ حالیہ ناکامیاں:\n{out}"