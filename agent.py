# ============================================================================
# agent.py — Jarvis v4.0 PRO
#
# Design rules:
#  1. FULL Gemini intelligence by default — thinking ON, model defaults.
#     Every knob only changes when YOU set it in .env (no hardcoding).
#  2. Jarvis NEVER acts by itself: no startup greeting, no auto browser
#     launch, no proactive speech. It waits silently for your command.
#     (JARVIS_AUTO_GREETING=1 / JARVIS_PREWARM=full opt back in if wanted.)
#
# Smart-speed defaults (env-overridable, only affect reaction timing):
#  - Tight endpointing (min 0.25s), preemptive generation, HIGH
#    end-of-speech sensitivity, context-window compression.
#
# Power features:
#  - Native-account browser control (CDP attach to your real Chrome/Edge).
#  - jarvis_system: lock/shutdown/brightness/volume/processes/clipboard/
#    run-commands/notes; jarvis_reminders: spoken reminders;
#    browser_restart_native, browser_run_js, window_snap_tool.
# ============================================================================
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
    """Build the Gemini realtime model from .env settings.

    Philosophy: NOTHING is hard-blocked. By default Gemini runs with its
    FULL native intelligence — thinking ON, model defaults everywhere.
    Every knob below only applies when you explicitly set it in .env.
    """
    kwargs = {
        "voice": os.getenv("JARVIS_VOICE", "Charon"),
        # Proactivity OFF = Jarvis NEVER speaks or acts on its own;
        # it waits for your command. (Set JARVIS_PROACTIVITY=1 to allow it.)
        "proactivity": os.getenv("JARVIS_PROACTIVITY", "0") == "1",
    }

    model = (os.getenv("JARVIS_LLM_MODEL") or "").strip()
    if model:
        kwargs["model"] = model          # e.g. gemini-live-2.5-flash-native-audio

    # ── Gemini thinking: ON by default (full intelligence) ──
    # Only applies if you explicitly set JARVIS_THINKING_BUDGET in .env:
    #   0  → thinking off (max speed, less depth)
    #   N  → cap thinking at N tokens (balanced)
    #  -1 / unset → Gemini's own default (smart)
    budget_raw = (os.getenv("JARVIS_THINKING_BUDGET") or "").strip()
    if budget_raw:
        try:
            budget = int(budget_raw)
        except ValueError:
            budget = -1
        if budget >= 0:
            kwargs["thinking_config"] = genai_types.ThinkingConfig(
                thinking_budget=budget,
            )

    # ── Temperature: unset → Gemini's default ──
    temp_raw = (os.getenv("JARVIS_TEMPERATURE") or "").strip()
    if temp_raw:
        try:
            kwargs["temperature"] = float(temp_raw)
        except ValueError:
            pass

    # ── How fast Jarvis reacts when you pause (env-tunable) ──
    # Defaults below only shape reaction speed, never intelligence.
    end_sens   = (os.getenv("JARVIS_END_SENSITIVITY") or "HIGH").strip().upper()
    start_sens = (os.getenv("JARVIS_START_SENSITIVITY") or "LOW").strip().upper()
    try:
        silence_ms = int(os.getenv("JARVIS_SILENCE_MS", "300") or 300)
        prefix_ms  = int(os.getenv("JARVIS_PREFIX_MS", "20") or 20)
        kwargs["realtime_input_config"] = genai_types.RealtimeInputConfig(
            automatic_activity_detection=genai_types.AutomaticActivityDetection(
                disabled=False,
                start_of_speech_sensitivity=(
                    genai_types.StartSensitivity.START_SENSITIVITY_HIGH
                    if start_sens == "HIGH"
                    else genai_types.StartSensitivity.START_SENSITIVITY_LOW
                ),
                end_of_speech_sensitivity=(
                    genai_types.EndSensitivity.END_SENSITIVITY_LOW
                    if end_sens == "LOW"
                    else genai_types.EndSensitivity.END_SENSITIVITY_HIGH
                ),
                prefix_padding_ms=prefix_ms,
                silence_duration_ms=silence_ms,
            ),
        )
    except Exception:
        pass  # fall back to plugin/Gemini defaults

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


async def _prewarm(browser: bool = False):
    """Optional background warm-up.

    Default: only SILENT index caching (app names + file index) — nothing
    opens, nothing happens on screen. browser=True additionally pre-launches
    the browser (a visible action), so it's strictly opt-in.
    """
    tasks = []
    try:
        from Jarvis_window_CTRL import smart_index, discover_apps
        tasks.append(asyncio.to_thread(discover_apps))
        tasks.append(smart_index())
    except Exception:
        pass
    if browser:
        try:
            from jarvis_browser import warmup as browser_warmup
            tasks.append(browser_warmup())
        except Exception:
            pass
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def entrypoint(ctx: agents.JobContext):
    ensure_temp()

    def _env_float(name: str, default: float) -> float:
        try:
            return float(os.getenv(name, "") or default)
        except ValueError:
            return default

    session = AgentSession(
        llm=build_realtime_llm(),
        # ── Reaction-speed knobs (env-overridable; these are just defaults) ──
        min_endpointing_delay=_env_float("JARVIS_MIN_ENDPOINTING", 0.25),
        max_endpointing_delay=_env_float("JARVIS_MAX_ENDPOINTING", 4.0),
        preemptive_generation=os.getenv("JARVIS_PREEMPTIVE", "1") != "0",
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

    # Auto-clean temp folder every hour (invisible housekeeping only)
    try:
        start_cleanup(interval_seconds=3600)
    except Exception:
        pass

    # ── Prewarm — strictly limited, nothing visible happens by itself ──
    #   JARVIS_PREWARM=index (default) → only silent cache building
    #   JARVIS_PREWARM=full            → also pre-launches the browser
    #   JARVIS_PREWARM=off             → do nothing at all
    prewarm_mode = (os.getenv("JARVIS_PREWARM") or "index").strip().lower()
    if prewarm_mode not in ("off", "0", "none"):
        try:
            asyncio.get_running_loop().create_task(
                _prewarm(browser=prewarm_mode in ("full", "browser", "1"))
            )
        except Exception:
            pass

    # ── Jarvis stays SILENT until YOUR first command ──
    # No startup greeting, no self-actions. Only with JARVIS_AUTO_GREETING=1
    # will it introduce itself when you connect.
    if os.getenv("JARVIS_AUTO_GREETING", "0") == "1":
        await session.generate_reply(
            instructions=Reply_prompts
        )


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
