# ============================================================================
# setup_jarvis_chrome.py — ONE-TIME setup for native-account browser control
#
# Creates a desktop shortcut "Jarvis Chrome" that launches YOUR Chrome with
# YOUR normal profile PLUS --remote-debugging-port=9222. With this shortcut,
# Jarvis attaches to your real browser (logged-in Gmail, YouTube, etc.)
# instead of a blank automation profile.
#
# Run once:  python setup_jarvis_chrome.py
# Then always start Chrome from that shortcut (or let Jarvis do
# "browser_restart_native" for you).
# ============================================================================
import os
import sys

# Console-safe output (cp1252 consoles choke on ✅/❌/Urdu)
try:
    # getattr: the stubs type stdout/stderr as TextIO, which has no
    # `reconfigure` (it exists only on the real TextIOWrapper).
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if callable(_reconfigure):
            _reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    import win32com.client
except ImportError:
    print("❌ pywin32 needed:  pip install pywin32")
    sys.exit(1)

CDP_PORT = os.getenv("JARVIS_CDP_PORT", "9222")

CHROME_PATHS = [
    os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]


def find_chrome():
    for p in CHROME_PATHS:
        if os.path.exists(p):
            return p
    return None


def main():
    chrome = find_chrome()
    if not chrome:
        print("❌ Chrome نہیں ملا — Chrome انسٹال کریں یا path چیک کریں۔")
        sys.exit(1)

    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    shortcut_path = os.path.join(desktop, "Jarvis Chrome.lnk")

    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(shortcut_path)
    shortcut.TargetPath = chrome
    shortcut.Arguments = f"--remote-debugging-port={CDP_PORT}"
    shortcut.IconLocation = chrome
    shortcut.Description = "Chrome with Jarvis automation port (native profile)"
    shortcut.Save()

    print(f"✅ Shortcut بن گیا: {shortcut_path}")
    print(f"   Target: {chrome}")
    print(f"   Args:   --remote-debugging-port={CDP_PORT}")
    print()
    print("ℹ️  اگلی بار Chrome اسی shortcut سے کھولیں (اپنی normal profile کے ساتھ)۔")
    print("   Jarvis پھر آپ کے اصلی browser کو control کرے گا — logged-in account سمیت۔")


if __name__ == "__main__":
    main()
