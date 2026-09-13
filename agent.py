import asyncio
import os

from dotenv import load_dotenv
from google.genai import types as genai_types

from livekit import agents
from livekit.agents import AgentSession, Agent, RoomInputOptions
from livekit.plugins import (
    google,
    noise_cancellation,
)

from Jarvis_prompts import behavior_prompts, Reply_prompts
from Jarvis_google_search import google_search, get_current_datetime
from jarvis_get_whether import get_weather
from Jarvis_window_CTRL import (
    open, close, folder_file, window_snap_tool,
    focus_window_tool, focus_browser_tool,
    get_active_window_tool,
    minimize_window_tool, maximize_window_tool,
)
from Jarvis_file_opner import Play_file
from keyboard_mouse_CTRL import (
    move_cursor_tool, move_cursor_to_tool,
    click_at_tool, mouse_click_tool,
    drag_mouse_tool, scroll_cursor_tool,
    get_cursor_position_tool,
    type_text_tool, press_key_tool,
    press_hotkey_tool, swipe_gesture_tool,
    control_volume_tool, list_windows_tool,
    take_screenshot_tool, get_screen_size_tool,
)
from jarvis_browser import (
    browser_open, browser_get_current_url, browser_get_page_summary,
    browser_google_search, browser_search_youtube, browser_play_youtube,
    browser_click_text, browser_click_selector,
    browser_find_and_click, browser_fill_and_submit,
    browser_type, browser_press_key,
    browser_scroll, browser_scroll_to_bottom, browser_wait,
    browser_get_text, browser_extract_links,
    browser_screenshot, browser_get_info,
    browser_new_tab, browser_list_tabs,
    browser_switch_tab, browser_close_tab,
    browser_go_back, browser_refresh,
    browser_research, browser_close,
    browser_restart_native, browser_run_js,
)
from jarvis_terminal import terminal_tool, terminal_run_powershell, terminal_pwd
from jarvis_temp import ensure_temp, start_cleanup
from jarvis_system import (
    system_control_tool, system_info_tool, media_control_tool,
    set_volume_tool, process_tool, clipboard_tool,
    run_command_tool, save_note_tool,
)
from jarvis_reminders import set_reminder_tool

load_dotenv()


def build_realtime_llm():
    """Gemini realtime model tuned for minimum first-word latency."""
    kwargs = {
        "voice": os.getenv("JARVIS_VOICE", "Charon"),
        "temperature": 0.7,
        "proactivity": False,            # no unsolicited chatter, faster turns
        "enable_affective_dialog": False,
    }
    model = (os.getenv("JARVIS_LLM_MODEL") or "").strip()
    if model:
        kwargs["model"] = model          # e.g. gemini-live-2.5-flash-native-audio

    # Thinking OFF by default → seconds faster on every reply.
    # Set JARVIS_THINKING_BUDGET=-1 to use the model default, or e.g. 512.
    try:
        budget = int(os.getenv("JARVIS_THINKING_BUDGET", "0"))
    except ValueError:
        budget = 0
    if budget >= 0:
        kwargs["thinking_config"] = genai_types.ThinkingConfig(
            thinking_budget=budget,
        )

    # Fast, server-side speech endpoint detection
    kwargs["realtime_input_config"] = genai_types.RealtimeInputConfig(
        automatic_activity_detection=genai_types.AutomaticActivityDetection(
            disabled=False,
            start_of_speech_sensitivity=genai_types.StartSensitivity.START_SENSITIVITY_LOW,
            end_of_speech_sensitivity=genai_types.EndSensitivity.END_SENSITIVITY_HIGH,
            prefix_padding_ms=20,
            silence_duration_ms=300,
        ),
    )

    # Long sessions stay fast (old context is compressed, not re-sent)
    try:
        kwargs["context_window_compression"] = (
            genai_types.ContextWindowCompressionConfig(
                sliding_window=genai_types.SlidingWindow(),
            )
        )
    except Exception:
        pass

    return google.beta.realtime.RealtimeModel(**kwargs)


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=behavior_prompts,
            tools=[
                # ── Search / Info ──────────────────────────────
                google_search,
                get_current_datetime,
                get_weather,

                # ── Window / App control ───────────────────────
                open,
                close,
                folder_file,
                Play_file,
                focus_window_tool,
                focus_browser_tool,
                get_active_window_tool,
                minimize_window_tool,
                maximize_window_tool,
                window_snap_tool,
                list_windows_tool,

                # ── System / power ─────────────────────────────
                system_control_tool,
                system_info_tool,
                process_tool,
                run_command_tool,

                # ── Mouse ─────────────────────────────────────
                move_cursor_tool,
                move_cursor_to_tool,
                click_at_tool,
                mouse_click_tool,
                drag_mouse_tool,
                scroll_cursor_tool,
                get_cursor_position_tool,
                swipe_gesture_tool,

                # ── Keyboard ──────────────────────────────────
                type_text_tool,
                press_key_tool,
                press_hotkey_tool,

                # ── Screen / media / volume ───────────────────
                take_screenshot_tool,
                get_screen_size_tool,
                control_volume_tool,
                set_volume_tool,
                media_control_tool,
                clipboard_tool,

                # ── Browser: navigation ───────────────────────
                browser_open,
                browser_get_current_url,
                browser_get_page_summary,
                browser_google_search,
                browser_search_youtube,
                browser_play_youtube,

                # ── Browser: interaction ──────────────────────
                browser_click_text,
                browser_click_selector,
                browser_find_and_click,
                browser_fill_and_submit,
                browser_type,
                browser_press_key,
                browser_scroll,
                browser_scroll_to_bottom,
                browser_wait,
                browser_run_js,

                # ── Browser: reading ──────────────────────────
                browser_get_text,
                browser_get_info,
                browser_extract_links,
                browser_screenshot,

                # ── Browser: tabs ─────────────────────────────
                browser_new_tab,
                browser_list_tabs,
                browser_switch_tab,
                browser_close_tab,
                browser_go_back,
                browser_refresh,
                browser_research,
                browser_restart_native,
                browser_close,

                # ── Terminal ──────────────────────────────────
                terminal_tool,
                terminal_run_powershell,
                terminal_pwd,

                # ── Productivity ──────────────────────────────
                set_reminder_tool,
                save_note_tool,
            ]
        )


async def _prewarm():
    """Background warm-up: browser launch + app index + file index — all parallel."""
    tasks = []
    try:
        from Jarvis_window_CTRL import smart_index, discover_apps
        tasks.append(asyncio.to_thread(discover_apps))
        tasks.append(smart_index())
    except Exception:
        pass
    try:
        from jarvis_browser import warmup as browser_warmup
        tasks.append(browser_warmup())
    except Exception:
        pass
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def entrypoint(ctx: agents.JobContext):
    ensure_temp()

    session = AgentSession(
        llm=build_realtime_llm(),
        # ── Latency tuning ──
        min_endpointing_delay=0.25,     # default 0.5s → reacts twice as fast
        max_endpointing_delay=4.0,      # don't wait forever for "more to come"
        preemptive_generation=True,     # start generating while you finish speaking
    )

    await session.start(
        room=ctx.room,
        agent=Assistant(),
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
            video_enabled=True,
        ),
    )

    await ctx.connect()

    # Auto-clean temp folder every hour
    try:
        start_cleanup(interval_seconds=3600)
    except Exception:
        pass

    # Fire-and-forget warm-up (zero cold-start on first command)
    try:
        asyncio.get_running_loop().create_task(_prewarm())
    except Exception:
        pass

    await session.generate_reply(
        instructions=Reply_prompts
    )


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
