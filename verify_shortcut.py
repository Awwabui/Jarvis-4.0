# Verify the created shortcut (run: venv\Scripts\python.exe verify_shortcut.py)
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import win32com.client

path = os.path.join(os.path.expanduser("~"), "Desktop", "Jarvis Chrome.lnk")
shell = win32com.client.Dispatch("WScript.Shell")
s = shell.CreateShortCut(path)
print("exists:", os.path.exists(path))
print("target:", s.TargetPath)
print("args:  ", s.Arguments)
print("desc:  ", s.Description)
print("target exists:", os.path.exists(s.TargetPath) if s.TargetPath else False)