# ============================================================================
# test_v41_tools.py — live smoke test for Jarvis v4.1 new tools
# (safe: uses temp folder, Recycle Bin delete, and harmless reads only)
# ============================================================================
import asyncio
import os
import sys

# getattr: the stubs type stdout as TextIO, which has no `reconfigure`.
_reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(_reconfigure):
    _reconfigure(encoding="utf-8", errors="replace")

import jarvis_ui
import jarvis_safety
import jarvis_planner
import jarvis_files
import jarvis_winops
import jarvis_notify


async def main():
    print("=" * 60)
    print("1) UI Automation: list windows")
    r = await jarvis_ui.ui_list_windows_tool()
    print(r[:600])

    print("=" * 60)
    print("2) Open Notepad and drive it via UIA")
    os.startfile("notepad")
    await asyncio.sleep(2.0)
    r = await jarvis_ui.ui_type_tool("notepad", "Hello from Jarvis UIA layer",
                                     control_type="Edit")
    print("type:", r)
    r = await jarvis_ui.ui_get_text_tool("notepad", control_type="Edit")
    print("read:", r[:200])
    r = await jarvis_ui.ui_wait_tool("notepad", timeout=5)
    print("wait:", r)
    r = await jarvis_ui.ui_find_tool("notepad", control_type="MenuBar")
    print("find menu:", r[:200])

    print("=" * 60)
    print("3) Planner loop")
    r = await jarvis_planner.create_plan_tool(
        "smoke test", ["open notepad", "type text", "verify text"])
    print(r[:200])
    r = await jarvis_planner.complete_step_tool(1, "notepad opened", True)
    print(r)
    r = await jarvis_planner.complete_step_tool(2, "typed via ui_type_tool", True)
    print(r)
    r = await jarvis_planner.get_plan_tool()
    print(r)
    r = await jarvis_planner.abandon_plan_tool("smoke test done")
    print(r)

    print("=" * 60)
    print("4) Emergency stop gates dangerous tools")
    print(await jarvis_safety.emergency_stop_tool())
    r = await jarvis_files.file_delete_tool("C:\\nonexistent_test.txt", confirm=True)
    print("delete while stopped:", r[:100])
    print(await jarvis_safety.jarvis_continue_tool())
    r = await jarvis_safety.recent_actions_tool(5)
    print(r[:400])

    print("=" * 60)
    print("5) File ops (temp folder)")
    tmp = os.path.join(os.path.expanduser("~"), "Documents", "Jarvis_SmokeTest")
    print(await jarvis_files.file_mkdir_tool(os.path.join(tmp, "sub")))
    with open(os.path.join(tmp, "sample.txt"), "w") as f:
        f.write("jarvis smoke test file")
    print(await jarvis_files.file_search_tool("sample.txt", tmp))
    print(await jarvis_files.file_move_tool(
        os.path.join(tmp, "sample.txt"), os.path.join(tmp, "sub", "renamed.txt")))
    print(await jarvis_files.recent_files_tool(tmp))
    print(await jarvis_files.file_delete_tool(tmp, confirm=True))  # → Recycle Bin

    print("=" * 60)
    print("6) System reads: services / network / power")
    r = await jarvis_winops.windows_service_tool("status", name="wuauserv")
    print(r[:250])
    r = await jarvis_winops.network_info_tool()
    print(r[:400])
    r = await jarvis_winops.power_plan_tool("status")
    print(r[:200])

    print("=" * 60)
    print("7) Toast + clips")
    print(await jarvis_notify.toast_notify_tool("Jarvis v4.1", "Smoke test running"))
    print(await jarvis_notify.clipboard_history_tool("save", name="smoke",
                                                     text="jarvis clip test"))
    print(await jarvis_notify.clipboard_history_tool("list"))

    print("=" * 60)
    print("ALL SMOKE TESTS DONE")


if __name__ == "__main__":
    asyncio.run(main())
