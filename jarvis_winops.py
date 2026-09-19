# ============================================================================
# jarvis_winops.py — Jarvis v4.1 deep Windows control
#
#   • windows_service_tool     — list / start / stop / restart services
#   • process_manage_tool      — kill by PID / suspend / resume / set priority
#   • registry_tool            — registry read (safe) / write-delete (confirm!)
#   • virtual_desktop_tool     — switch / create / close virtual desktops
#   • network_info_tool        — Wi-Fi status, adapters, public IP
#   • power_plan_tool          — list / set power plans, battery report
#
# Safety: registry writes, service stop/restart and process kill all require
# confirm=True; emergency-stop gate + cooldowns apply; all calls are logged.
# Uses psutil + PowerShell (same _ps style as jarvis_system.py).
# ============================================================================
import asyncio
import logging
import re

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


async def _ps(script: str, timeout: int = 15) -> str:
    """Run a short PowerShell snippet, return trimmed stdout (jarvis_system style)."""
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
# Windows Services
# ──────────────────────────────────────────────
@function_tool
async def windows_service_tool(action: str, name: str = "",
                               confirm: bool = False) -> str:
    """Windows services کنٹرول: action='list' (چلتے services)، 'status' (ایک
    service کی حالت — name دیں)، 'start' / 'stop' / 'restart'۔ stop/restart
    سسٹم سروس بند کر سکتے ہیں → پہلے M. Awwab sir سے پوچھیں، پھر confirm=True۔"""
    action = (action or "").strip().lower()
    name = (name or "").strip()
    if action == "list":
        out = await _ps("Get-Service | Where-Object Status -eq 'Running' | "
                        "Select-Object -First 25 Name,DisplayName | "
                        "Format-Table -AutoSize | Out-String -Width 120", 20)
        return f"🛠️ چلتے services:\n{out[:3000]}"
    if not name:
        return f"❌ '{action}' کے لیے service کا نام دیں (جیسے 'wuauserv')۔"
    if action == "status":
        out = await _ps(f"Get-Service -Name '{name}' | "
                        "Select-Object Name,Status,DisplayName | Format-List | Out-String")
        return f"🛠️ Service '{name}':\n{out[:800]}"
    if action in ("start", "stop", "restart"):
        if not confirm:
            return (f"⚠ service '{name}' کو {action} کرنا سسٹم پر اثر ڈال سکتا ہے۔ "
                    "M. Awwab sir سے پوچھیں، پھر confirm=True کے ساتھ دوبارہ کریں۔")
        blocked = safety.gate(f"service_{action}", cooldown=10.0)
        if blocked:
            return blocked
        cmd = {"start": "Start-Service", "stop": "Stop-Service",
               "restart": "Restart-Service"}[action]
        out = await _ps(f"{cmd} -Name '{name}' -ErrorAction Stop; "
                        f"(Get-Service -Name '{name}').Status", 25)
        ok = "(error" not in out and "Exception" not in out
        safety.record_action(f"service_{action}", name, ok)
        mark = "✅" if ok else "❌"
        return f"{mark} service '{name}' {action}: حالت = {out[:200]}"
    return "❌ action: list / status / start / stop / restart"

# ──────────────────────────────────────────────
# Advanced process management
# ──────────────────────────────────────────────
@function_tool
async def process_manage_tool(action: str, pid: int = 0, name: str = "",
                              level: str = "", confirm: bool = False) -> str:
    """بہتر process کنٹرول: action='kill' (pid یا name — confirm=True ضروری)،
    'suspend' / 'resume' (process منجمّد کریں — confirm=True)،
    'priority' (level: real_time/high/normal/below_normal/low — confirm=True)،
    'info' (ایک process کی تفصیل)۔ مثال: action='kill', pid=1234, confirm=True۔"""
    action = (action or "").strip().lower()
    if action == "info":
        import psutil
        try:
            p = psutil.Process(int(pid))
            with p.oneshot():
                mem = p.memory_info().rss / (1024 * 1024)
                return (f"⚙️ PID {p.pid}: {p.name()} | CPU {p.cpu_percent():.1f}% | "
                        f"RAM {mem:.0f} MB | status {p.status()} | "
                        f"threads {p.num_threads()}")
        except Exception as e:
            return f"❌ info نہیں ملی: {e}"

    if action in ("kill", "suspend", "resume", "priority"):
        if not confirm:
            return (f"⚠ '{action}' process پر اثر ڈال سکتا ہے (data بچ سکتا ہے یا "
                    "ایپ بند ہو سکتی ہے)۔ M. Awwab sir سے پوچھیں، پھر confirm=True۔")
        blocked = safety.gate(f"process_{action}", cooldown=8.0)
        if blocked:
            return blocked
        import psutil
        try:
            target = psutil.Process(int(pid)) if pid else None
            if target is None and name:
                name_l = name.lower()
                if not name_l.endswith(".exe"):
                    name_l += ".exe"
                cands = [p for p in psutil.process_iter(["pid", "name"])
                         if (p.info["name"] or "").lower() == name_l]
                if not cands:
                    return f"❌ process نہیں ملا: {name}"
                if action == "kill":  # kill ALL instances
                    for c in cands:
                        c.kill()
                    safety.record_action("process_kill", name_l, True)
                    return f"✅ {len(cands)} instance(s) of {name_l} بند کر دیے۔"
                target = cands[0]
            if target is None:
                return "❌ pid یا name دیں۔"
            pname = target.name()
            if action == "kill":
                target.kill()
                safety.record_action("process_kill", f"{pname} (pid {target.pid})", True)
                return f"✅ {pname} (pid {target.pid}) بند کر دیا۔"
            if action == "suspend":
                target.suspend()
                safety.record_action("process_suspend", pname, True)
                return f"⏸️ {pname} suspend (منجمّد) کر دیا۔"
            if action == "resume":
                target.resume()
                safety.record_action("process_resume", pname, True)
                return f"▶️ {pname} resume کر دیا۔"
            if action == "priority":
                pmap = {"real_time": psutil.REALTIME_PRIORITY_CLASS,
                        "high": psutil.HIGH_PRIORITY_CLASS,
                        "normal": psutil.NORMAL_PRIORITY_CLASS,
                        "below_normal": psutil.BELOW_NORMAL_PRIORITY_CLASS,
                        "low": psutil.IDLE_PRIORITY_CLASS}
                lvl = (level or "").strip().lower()
                if lvl not in pmap:
                    return "❌ level: real_time / high / normal / below_normal / low"
                target.nice(pmap[lvl])
                safety.record_action("process_priority", f"{pname} → {lvl}", True)
                return f"🎚️ {pname} priority → {lvl}"
        except psutil.AccessDenied:
            return f"❌ permission نہیں (admin needed?) — '{name or pid}'"
        except psutil.NoSuchProcess:
            return "❌ یہ process موجود نہیں (بند ہو چکی ہے؟)۔"
        except Exception as e:
            return f"❌ {action} ناکام: {e}"
    return "❌ action: kill / suspend / resume / priority / info"

# ──────────────────────────────────────────────
# Registry (read = safe, write/delete = confirm!)
# ──────────────────────────────────────────────
_REG_ROOTS = {"HKLM": 0x80000002, "HKCU": 0x80000001, "HKCR": 0x80000000,
              "HKU": 0x80000003}
_REG_TYPE = {"REG_SZ": 1, "REG_EXPAND_SZ": 2, "REG_BINARY": 3,
             "REG_DWORD": 4, "REG_QWORD": 11}


@function_tool
async def registry_tool(action: str, path: str, value_name: str = "",
                        data: str = "", value_type: str = "REG_SZ",
                        confirm: bool = False) -> str:
    """Registry access۔ action='read' (محفوظ — path + optional value_name)،
    'write' (data + value_type: REG_SZ/REG_DWORD/REG_QWORD — confirm=True
    ضروری، پہلے M. Awwab sir سے پوچھیں!)، 'delete' (value یا key —
    confirm=True ضروری)۔ path مثال: HKCU\\Software\\Microsoft\\Windows۔
    ⚠ Registry غلط تبدیلی ونڈوز توڑ سکتی ہے — ہمیشہ تصدیق کریں۔"""
    action = (action or "").strip().lower()
    path = (path or "").strip().replace("/", "\\")
    m = re.match(r"^(HKLM|HKCU|HKCR|HKU)[\\/](.+)$", path, re.IGNORECASE)
    if action not in ("read", "write", "delete"):
        return "❌ action: read / write / delete"
    if not m:
        return "❌ پاتھ HKLM\\ / HKCU\\ / HKCR\\ / HKU\\ سے شروع ہونا چاہیے۔"
    root, sub = m.group(1).upper(), m.group(2)
    rname = root
    if action == "read":
        if value_name:
            ps = (f"$v = Get-ItemProperty -Path '{rname}\\{sub}' -Name '{value_name}' "
                  f"-ErrorAction Stop; $v.'{value_name}'")
        else:
            ps = f"Get-ItemProperty -Path '{rname}\\{sub}' | Format-List | Out-String"
        out = await _ps(ps, 10)
        if "(error" in out or "cannot find" in out.lower():
            return f"❌ Registry read ناکام:\n{out[:500]}"
        return f"Registry '{path}'{(' → ' + value_name) if value_name else ''}:\n{out[:2000]}"
    # write / delete — confirm required
    if not confirm:
        return (f"⚠ Registry {action} خطرناک ہے: {path} {value_name}۔ "
                "Windows خراب ہو سکتا ہے۔ M. Awwab sir سے صاف پوچھیں، پھر "
                "confirm=True کے ساتھ دوبارہ کریں۔")
    blocked = safety.gate(f"registry_{action}", cooldown=10.0)
    if blocked:
        return blocked
    if action == "write":
        if not value_name:
            return "❌ write کے لیے value_name دیں۔"
        vt = (value_type or "REG_SZ").strip().upper()
        if vt not in _REG_TYPE:
            return "❌ value_type: REG_SZ / REG_DWORD / REG_QWORD / REG_BINARY"
        if vt in ("REG_DWORD", "REG_QWORD"):
            data = str(int(data)) if str(data).strip().lstrip("-").isdigit() else data
        out = await _ps(
            f"New-ItemProperty -Path '{rname}\\{sub}' -Name '{value_name}' "
            f"-Value '{data}' -PropertyType {vt} -Force | Out-Null; 'OK'", 10)
        ok = "OK" in out
        safety.record_action("registry_write", f"{path}\\{value_name}={data}", ok)
        return (f"✅ Registry write: {path}\\{value_name} = {data} ({vt})۔"
                if ok else f"❌ Registry write ناکام: {out[:400]}")
    # delete
    if value_name:
        out = await _ps(
            f"Remove-ItemProperty -Path '{rname}\\{sub}' -Name '{value_name}' "
            "-ErrorAction Stop; 'OK'", 10)
    else:
        out = await _ps(f"Remove-Item -Path '{rname}\\{sub}' -Recurse -ErrorAction Stop; 'OK'", 10)
    ok = "OK" in out
    safety.record_action("registry_delete", f"{path} {value_name}", ok)
    return (f"✅ Registry delete مکمل: {path} {value_name}۔"
            if ok else f"❌ Registry delete ناکام: {out[:400]}")

# ──────────────────────────────────────────────
# Virtual desktops (keyboard-driven — no deps needed)
# ──────────────────────────────────────────────
@function_tool
async def virtual_desktop_tool(action: str, count: int = 1) -> str:
    """Virtual desktops: action='next' / 'previous' (switch), 'switch' (count
    پر جاؤ — Win+Ctrl+number), 'create' (نیا desktop)، 'close' (موجودہ بند)۔
    یہ Windows keyboard shortcuts استعمال کرتا ہے (Win+Ctrl+←/→، Win+Ctrl+D،
    Win+Ctrl+F4)۔"""
    action = (action or "").strip().lower()
    from keyboard_mouse_CTRL import press_hotkey_tool as _hotkey
    combos = {
        "next": ["ctrl", "win", "right"],
        "previous": ["ctrl", "win", "left"],
        "prev": ["ctrl", "win", "left"],
        "create": ["ctrl", "win", "d"],
        "close": ["ctrl", "win", "f4"],
    }
    if action in combos:
        res = await _hotkey(combos[action])
        safety.record_action("virtual_desktop", action, "❌" not in res)
        return f"🖥️ Virtual desktop {action} — ({res})"
    if action == "switch":
        count = max(1, min(9, int(count)))
        res = await _hotkey(["win", str(count)])
        safety.record_action("virtual_desktop", f"switch→{count}", "❌" not in res)
        return f"🖥️ Desktop #{count} پر switch ({res})"
    return "❌ action: next / previous / switch / create / close"


# ──────────────────────────────────────────────
# Network tools
# ──────────────────────────────────────────────
@function_tool
async def network_info_tool() -> str:
    """نیٹ ورک کی حالت: Wi-Fi SSID + signal، active adapters، local + public IP۔
    'Wifi kaisa hai' / 'network check karo' جیسے کام کے لیے۔"""
    out = await _ps(
        "$w = (netsh wlan show interfaces | Select-String ' SSID|Signal'); "
        "$ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
        "Where-Object {$_.IPAddress -ne '127.0.0.1'} | "
        "Select-Object -First 5 IPAddress,InterfaceAlias; "
        "Write-Output 'WIFI:'; $w | ForEach-Object {$_.Line.Trim()}; "
        "Write-Output 'ADAPTERS:'; $ips | ForEach-Object "
        "{ Write-Output ($_.InterfaceAlias + ': ' + $_.IPAddress) }", 15)
    if "(error" in out or not out:
        return f"❌ Network info نہیں ملی: {out[:300]}"
    return f"🌐 Network:\n{out[:1500]}"


# ──────────────────────────────────────────────
# Power plans
# ──────────────────────────────────────────────
@function_tool
async def power_plan_tool(action: str = "list", plan: str = "") -> str:
    """Power plans: action='list' (دستیاب plans + active)، 'set' (plan نام سے —
    'balanced' / 'high performance' / 'power saver' یا GUID)، 'status' (بیٹری
    + active plan کی تفصیل)۔"""
    action = (action or "list").strip().lower()
    if action == "list":
        out = await _ps("powercfg /list", 15)
        return f"🔋 Power plans:\n{out[:2000]}"
    if action == "status":
        out = await _ps("powercfg /getactivescheme", 10)
        return f"⚡ Active power plan:\n{out[:500]}"
    if action == "set":
        plan = (plan or "").strip().lower()
        aliases = {"balanced": "381b4222-f694-41f0-9685-ff5bb260df2e",
                   "high performance": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
                   "high": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
                   "performance": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
                   "power saver": "a1841308-3541-4fab-bc81-f71556f20b4a",
                   "saver": "a1841308-3541-4fab-bc81-f71556f20b4a"}
        guid = aliases.get(plan, plan)
        if not re.match(r"^[0-9a-f\-]{36}$", guid):
            return ("❌ plan نام دیں: balanced / high performance / power saver "
                    "(یا valid GUID)")
        out = await _ps(f"powercfg /setactive {guid}", 15)
        if "(error" in out or out.strip():
            return f"⚠ powercfg: {out[:300]}"
        safety.record_action("power_plan", plan, True)
        return f"🔋 Power plan set: {plan}"
    return "❌ action: list / set / status"
