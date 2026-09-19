# ============================================================================
# setup_jarvis_desktop.py — ONE-TIME setup for the Jarvis desktop app
#
# Creates a desktop shortcut "Jarvis Desktop" that opens jarvis_gui.py with the
# project's own virtual-env python (no console window), so Jarvis behaves like
# a normal Windows app: double-click → window opens → press Start Jarvis.
#
# Run once:  venv\Scripts\python.exe setup_jarvis_desktop.py
# (also available inside the app: Tools → Create desktop shortcut)
# ============================================================================
import os
import sys

# Console-safe output (cp1252 consoles choke on Urdu / emoji)
try:
    # getattr: the stubs type stdout/stderr as TextIO, which has no
    # `reconfigure` (it exists only on the real TextIOWrapper).
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if callable(_reconfigure):
            _reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
GUI_SCRIPT = os.path.join(PROJECT_DIR, "jarvis_gui.py")
VENV_PYTHONW = os.path.join(PROJECT_DIR, "venv", "Scripts", "pythonw.exe")
VENV_PYTHON = os.path.join(PROJECT_DIR, "venv", "Scripts", "python.exe")
SHORTCUT_NAME = "Jarvis Desktop.lnk"


def _pick_python() -> str:
    """pythonw.exe (no console) when the venv exists, else this interpreter."""
    if os.path.exists(VENV_PYTHONW):
        return VENV_PYTHONW
    if os.path.exists(VENV_PYTHON):
        return VENV_PYTHON
    base = os.path.dirname(sys.executable or "")
    cand = os.path.join(base, "pythonw.exe")
    return cand if os.path.exists(cand) else (sys.executable or "python")


def main() -> int:
    if not os.path.exists(GUI_SCRIPT):
        print(f"❌ jarvis_gui.py nahi mila: {GUI_SCRIPT}")
        return 1

    try:
        import win32com.client
    except ImportError:
        print(" pywin32 needed:  pip install pywin32")
        return 1

    python = _pick_python()
    shortcut_path = os.path.join(os.path.expanduser("~"), "Desktop", SHORTCUT_NAME)

    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(shortcut_path)
    shortcut.TargetPath = python
    shortcut.Arguments = f'"{GUI_SCRIPT}"'
    shortcut.WorkingDirectory = PROJECT_DIR
    shortcut.IconLocation = f"{python},0"
    shortcut.Description = "Jarvis 4.0 — desktop voice assistant (Start par agent.py khud chalta hai)"
    shortcut.Save()

    print(f"✅ Shortcut بن گیا: {shortcut_path}")
    print(f"   Target: {python}")
    print(f"   Args:   \"{GUI_SCRIPT}\"")
    print()
    print("ℹ️  Desktop par 'Jarvis Desktop' double-click karein:")
    print("   window khulegi → ▶ Start Jarvis dabayein → mic se baat karein.")
    return 0


if __name__ == "__main__":
    sys.exit(main())