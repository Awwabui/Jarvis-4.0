# ============================================================================
# jarvis_planner.py — Jarvis v4.1 INTELLIGENCE layer
# (Planner + Executor + Verifier + short-term action memory)
#
# Multi-step tasks follow this loop (enforced via prompts + tool docstrings):
#
#     1. PLAN    → create_plan_tool(task, steps=[...])   چھوٹے clear steps
#     2. EXECUTE → per step: ek UIA/browser/keyboard tool call
#     3. VERIFY  → get_screen_context_tool / ui_get_text_tool / ui_find_tool
#                  سے نتیجہ confirm کریں (اندازے پر یقین نہ کریں)
#     4. RECORD  → complete_step_tool(step_no, result, success)
#                  fail → reason + ALAG approach (repeat نہیں)
#     5. NEXT    → get_plan_tool سے اگلا step
#
# State is in-memory per agent process (no files). Emergency-stop aware:
# while the kill switch is tripped, plan execution is halted too.
# ============================================================================
import time
import logging
from typing import List

from livekit.agents import function_tool

import jarvis_safety as safety

logger = logging.getLogger(__name__)

_MAX_STEPS = 12
_plan = None  # {"task","steps":[{no,text,status,result}],"created","current"}


def _active_plan():
    return _plan if _plan and _plan.get("current") is not None else None


@function_tool
async def create_plan_tool(task: str, steps: List[str]) -> str:
    """MULTI-STEP task کو plan میں توڑیں — صرف tabدیل کام کے لیے (جیسے "file
    rename کر کے email کھولو"، "settings میں جا کر X on کرو")۔ steps میں چھوٹے،
    clear، ایک action والے steps دیں (max 12)۔ Simple یک-کمانڈ کاموں کے لیے
    plan نہ بنائیں۔ پہلے failed_actions_tool دیکھیں تاکہ پرانی غلطی دہرائی نہ جائے۔"""
    global _plan
    if safety.is_stopped():
        return "🛑 EMERGENCY STOP فعال ہے — پہلے 'Jarvis continue'۔"
    steps = [s.strip() for s in (steps or []) if s and s.strip()]
    if not task or not task.strip():
        return "❌ task کی وضاحت دیں۔"
    if not steps:
        return "❌ کم از کم ایک step دیں۔"
    if len(steps) > _MAX_STEPS:
        steps = steps[:_MAX_STEPS]
    _plan = {
        "task": task.strip(),
        "steps": [{"no": i + 1, "text": s, "status": "pending", "result": ""}
                  for i, s in enumerate(steps)],
        "current": 1,
        "created": time.time(),
    }
    fails = safety.last_failures(3)
    warn = ""
    if fails:
        warn = ("\n⚠ حالیہ ناکامیاں (دہرانے سے گریز کریں):\n"
                + safety._format_trail(fails))
    return (f"🗂️ Plan بن گیا: '{task.strip()}' ({len(steps)} steps)\n"
            + "\n".join(f"  {s['no']}. {s['text']}" for s in _plan["steps"])
            + "\nاب step 1 execute کریں → verify کریں → complete_step_tool۔" + warn)


@function_tool
async def get_plan_tool() -> str:
    """موجودہ plan کی حالت دیکھیں (اگلا step، ہر step کا status)۔ step
    execute کرنے سے پہلے اور بعد میں یہ کال کریں۔ کوئی plan نہ ہو تو boltا
    ہے — تب سیدھی action کریں۔"""
    p = _active_plan()
    if not p:
        return "(کوئی فعال plan نہیں — اگلا کام سیدھا کریں یا create_plan_tool استعمال کریں)"
    lines = [f"🗂️ Plan: {p['task']}"]
    for s in p["steps"]:
        mark = {"pending": "⬜", "done": "✅", "failed": "❌",
                "skipped": "⏭️"}.get(s["status"], "⬜")
        line = f"{mark} {s['no']}. {s['text']}"
        if s["result"]:
            line += f"  → {s['result'][:80]}"
        lines.append(line)
    cur = p["current"]
    if cur <= len(p["steps"]):
        lines.append(f"▶️ اگلا step: {cur} — '{p['steps'][cur - 1]['text']}'")
    else:
        lines.append("🏁 تمام steps مکمل!")
    return "\n".join(lines)


@function_tool
async def complete_step_tool(step_no: int, result: str, success: bool) -> str:
    """Step کا نتیجہ ریکارڈ کریں — صرف VERIFY کرنے کے بعد (screen context /
    ui_get_text_tool / ui_find_tool سے confirm)۔ success=False تو result میں
    وجہ لکھیں — action memory میں جا کر دوبارہ وہی غلطی نہ ہو۔"""
    p = _active_plan()
    if not p:
        return "(کوئی فعال plan نہیں — نتیجہ ریکارڈ نہیں ہو سکتا)"
    try:
        step_no = int(step_no)
    except (TypeError, ValueError):
        return "❌ step_no عدد ہونا چاہیے۔"
    step = next((s for s in p["steps"] if s["no"] == step_no), None)
    if not step:
        return f"❌ step {step_no} اس plan میں نہیں۔ (get_plan_tool دیکھیں)"
    step["status"] = "done" if success else "failed"
    step["result"] = (result or "")[:300]
    safety.record_action(f"plan_step_{step_no}", f"{step['text']} → {result}", success)
    if success and step_no == p["current"]:
        p["current"] = step_no + 1
        if p["current"] <= len(p["steps"]):
            return (f"✅ Step {step_no} مکمل۔ اگلا: {p['current']}. "
                    f"'{p['steps'][p['current'] - 1]['text']}'")
        return "🏁 تمام steps مکمل — task done! M. Awwab sir کو خلاصہ بتائیں۔"
    if not success:
        return (f"❌ Step {step_no} fail ریکارڈ ہوا۔ ALAG approach آزمائیں یا "
                "abandon_plan_tool کریں — وہی طریقہ دہرائیں نہیں۔")
    return f"✅ Step {step_no} مکمل۔"


@function_tool
async def abandon_plan_tool(reason: str = "") -> str:
    """Plan چھوڑ دیں — جب task ناممکن ہو جائے یا user دوسرا کام دے۔
    reason میں بتائیں کہ کیوں (user کو سادہ الفاظ میں بتائیں گے)۔"""
    global _plan
    p = _plan
    _plan = None
    if not p:
        return "(کوئی plan موجود نہیں تھا)"
    logger.info(f"Plan abandoned: {p['task']} ({reason})")
    return f"🗑️ Plan '{p['task']}' چھوڑ دیا۔ {('وجہ: ' + reason) if reason else ''}"
