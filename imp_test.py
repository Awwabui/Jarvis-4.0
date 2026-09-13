import importlib
mods=['livekit.agents','livekit.plugins.google','livekit.plugins.noise_cancellation','pyautogui','pynput','pygetwindow','fuzzywuzzy','Jarvis_google_search','jarvis_get_whether','Jarvis_window_CTRL','Jarvis_file_opner','keyboard_mouse_CTRL','agent']
for m in mods:
    try:
        importlib.import_module(m); print('OK   ', m)
    except Exception as e:
        print('FAIL ', m, '->', repr(e))
print('DONE')

