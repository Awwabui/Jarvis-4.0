# Test harness for Jarvis v4.0 PRO tools (safe, read-only tests).
# Run:  venv\Scripts\python.exe test_v4_tools.py
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import jarvis_terminal as jt

# ── Part 1: _clean_ps unit checks ──
s_plain = '#< CLIXML\r\n\r\n5:46:39 AM\r\n\r\n\r\n<Objs Version="1.1.0.1"><Obj S="progress"><AV>Preparing</AV></Obj></Objs>'
s_err = ('#< CLIXML\r\n<Objs><Obj S="progress"><AV>Preparing modules for first use.</AV></Obj>'
         '<S S="Error">Get-ChildItem : Cannot find path \'C:/nope\' because it does not exist._x000D__x000A_</S>'
         '<S S="Error">    + CategoryInfo : ObjectNotFound_x000D__x000A_</S></Objs>')
s_mixed = '#< CLIXML\r\nok\r\n<Objs><S S="Error">boom 2</S></Objs>'
print("clean plain:", repr(jt._clean_ps(s_plain)))
print("clean error:", repr(jt._clean_ps(s_err)))
print("clean mixed:", repr(jt._clean_ps(s_mixed)))
print("clean passthrough:", repr(jt._clean_ps("normal output")))
print("blocked (format):", jt._blocked_reason("format c:"))
print("blocked (rm -rf):", jt._blocked_reason("rm -rf x"))
print("blocked (remove-item):", jt._blocked_reason("Remove-Item -Recurse -Force x"))
print("safe cmd (None):", jt._blocked_reason("Get-Process | Select -First 3"))


# ── Part 2: live tool invocation ──
async def call(tool, *args, **kwargs):
    return await tool._func(*args, **kwargs)


async def main():
    from jarvis_system import system_info_tool, process_tool, clipboard_tool
    from jarvis_terminal import terminal_run_powershell, terminal_tool
    from Jarvis_window_CTRL import window_snap_tool

    print("=== system_info_tool ===")
    print((await call(system_info_tool))[:220])
    print("=== process_tool list ===")
    print((await call(process_tool, action="list"))[:220])
    print("=== clipboard get ===")
    print((await call(clipboard_tool, action="get"))[:120])
    print("=== powershell (EncodedCommand + CLIXML clean) ===")
    print((await call(terminal_run_powershell, script="Get-Date -DisplayHint Time"))[:200])
    print("=== powershell (quotes + braces robustness) ===")
    print((await call(terminal_run_powershell, script="Write-Output ('a \"quoted\" {brace} test')"))[:200])
    print("=== powershell (real error path) ===")
    print((await call(terminal_run_powershell, script="Get-ChildItem 'C:/does-not-exist-xyz'"))[:300])
    print("=== terminal cd . ===")
    print((await call(terminal_tool, command="cd ."))[:100])
    print("=== window_snap (missing window → clean error) ===")
    print(await call(window_snap_tool, window_title="xyz-not-a-window", position="left"))


asyncio.run(main())
print("LIVE TOOL TESTS DONE")






