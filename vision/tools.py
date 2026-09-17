# ============================================================================
# vision/tools.py — LiveKit function_tools exposed to the Jarvis agent
#
#   get_screen_context_tool        → Level 1: latest background context
#   analyze_screen_tool            → Level 2: fresh capture + detailed analysis
#   take_screenshot_desktop_tool   → explicit screenshot → Desktop PNG
#
# Follows the existing tool conventions (async, to_thread for blocking work,
# Urdu/Roman-Urdu style results, ❌/📸 emojis, never raise).
# ============================================================================
import asyncio

from livekit.agents import function_tool

from vision import screen_capture, screen_watcher, vision_agent, vision_prompts


@function_tool
async def get_screen_context_tool() -> str:
    """ابھی اسکرین پر کیا دکھ رہا ہے (background awareness)۔ 'What's wrong?',
    'What am I looking at?', 'What is this error?', 'What should I click?'
    جیسے سوالات جب سکرین دیکھنے کی ضرورت ہو تو یہ tool پہلے استعمال کریں۔
    فوری رہتا ہے — نئی screenshot نہیں لیتا۔"""
    return screen_watcher.build_context_message()


@function_tool
async def analyze_screen_tool(question: str = "") -> str:
    """تفصیلی سکرین تجزیہ — ابھی فوری نئی screenshot لے کر vision AI سے
    جواب حاصل کریں۔ 'Explain this error in detail', 'Read this', 'What does
    this say?' جیسے تفصیلی سوالات کے لیے۔ question میں user کا اصل سوال
    پاس کریں (خالی ساتھ عمومی تجزیہ ملتا ہے)۔"""
    def _analyze():
        img = screen_capture.capture_screen()
        if img is None:
            raise RuntimeError("Screen capture failed.")
        png = screen_capture.to_vision_png(img)
        return vision_agent.analyze_image(png, question or vision_prompts.QUICK_PROMPT)

    try:
        result = await asyncio.to_thread(_analyze)
        return f"🔍 سکرین تجزیہ:\n{result}"
    except vision_agent.VisionUnavailable as e:
        return f"❌ Vision unavailable: {e} (Jarvis bina vision kaam kar raha hai)"
    except Exception as e:
        return f"❌ Vision analysis failed: {e}"


@function_tool
async def take_screenshot_desktop_tool() -> str:
    """اسکرین شاٹ لیں اور Desktop پر PNG save کریں — صرف تب جب user صراحتاً
    screenshot مانگے۔ پاس ہونے والا نتیجہ میں فائل کا نام Desktop par batayein۔"""
    def _save():
        return screen_capture.save_screenshot_to_desktop()

    try:
        path = await asyncio.to_thread(_save)
        return f"📸 Screenshot saved to your Desktop as: {path}"
    except Exception as e:
        return f"❌ I couldn't save the screenshot to your Desktop. ({e})"