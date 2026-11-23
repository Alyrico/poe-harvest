"""
Windows-only parts: DPI awareness, the hotkey listener, the Path of Exile window, the popup window,
the tray icon and sounds. Nothing here sends input to the game or reads its memory.

One "ui" thread owns the hooks, the popup window and the tray icon (all need a Windows message
loop); other threads talk to it through Ui's methods, which post messages to it.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import datetime as dt
import os
import threading
import time
import winsound

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


def set_dpi_awareness():
    """Real pixels, so the 2560x1440 game is captured and the popup placed at true size."""
    try:
        fn = user32.SetProcessDpiAwarenessContext
        fn.argtypes = [ctypes.c_void_p]
        fn.restype = wt.BOOL
        if fn(ctypes.c_void_p(-4)):
            return
    except AttributeError:
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        user32.SetProcessDPIAware()
    except AttributeError:
        pass


LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t

# ---------------------------------------------------------------------------
# Path of Exile window
# ---------------------------------------------------------------------------
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowW.restype = wt.HWND
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wt.HWND
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]


def find_poe_window():
    for cls, title in (("POEWindowClass", None), (None, "Path of Exile")):
        hwnd = user32.FindWindowW(cls, title)
        if hwnd and user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            return hwnd
    return None


def poe_region(hwnd):
    rc, pt = wt.RECT(), wt.POINT(0, 0)
    if not user32.GetClientRect(hwnd, ctypes.byref(rc)) or not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    w, h = rc.right - rc.left, rc.bottom - rc.top
    if w < 100 or h < 100:
        return None
    return {"left": pt.x, "top": pt.y, "width": w, "height": h}


WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]


def window_title_starting(prefix: str) -> bool:
    """Is any window open whose title starts with `prefix`? (e.g. the old screenshot tool)"""
    found = []

    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value.startswith(prefix):
            found.append(hwnd)
            return False
        return True
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return bool(found)


def cursor_pos():
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def foreground():
    return user32.GetForegroundWindow()


# ---------------------------------------------------------------------------
# Sounds: short tones made on the fly, so the volume can be set (Beep() has no volume)
# ---------------------------------------------------------------------------
SOUNDS = {"node": [(1046, 90)],                              # one high beep
          "result": [(1046, 70), (0, 15), (1397, 90)],       # two rising beeps
          "buzz": [(220, 350)]}                              # low buzz: something there but unreadable
_sound_cache = {}
sound_settings = {"on": True, "volume": 70}


def _tone(parts, volume):
    import io
    import math
    import struct
    import wave
    rate = 22050
    amp = int(32767 * 0.6 * max(0, min(100, volume)) / 100)
    frames = bytearray()
    for freq, ms in parts:
        n = int(rate * ms / 1000)
        fade = int(rate * 0.006)
        for i in range(n):
            env = min(1.0, i / fade, (n - i) / fade) if fade else 1.0
            v = int(amp * env * math.sin(2 * math.pi * freq * i / rate)) if freq else 0
            frames += struct.pack("<h", v)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def play(sound):
    """Play without waiting (on its own short-lived thread)."""
    if not sound or sound not in SOUNDS or not sound_settings["on"] or sound_settings["volume"] <= 0:
        return
    key = (sound, sound_settings["volume"])
    if key not in _sound_cache:
        _sound_cache[key] = _tone(SOUNDS[sound], sound_settings["volume"])
    data = _sound_cache[key]

    def run():
        try:
            winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_NODEFAULT)
        except RuntimeError:
            pass
    threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------------------
# Hotkeys. A key is matched by its physical key (scan code + extended flag), so Numpad 1 works with
# NumLock on or off and isn't confused with End; by its virtual-key code when a program (e.g. mouse
# software) sends it without a scan code. Mouse side/middle buttons: {"mouse": "x1"/"x2"/"middle"}.
# ---------------------------------------------------------------------------
WH_KEYBOARD_LL, WH_MOUSE_LL, HC_ACTION = 13, 14, 0
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
WM_MBUTTONDOWN, WM_MBUTTONUP, WM_XBUTTONDOWN, WM_XBUTTONUP = 0x0207, 0x0208, 0x020B, 0x020C
LLKHF_EXTENDED, LLKHF_INJECTED = 0x01, 0x10
VK_ESCAPE = 0x1B
REPEAT_GAP = 0.6
NUMPAD_SCAN_TO_VK = {0x47: 0x67, 0x48: 0x68, 0x49: 0x69, 0x4B: 0x64, 0x4C: 0x65, 0x4D: 0x66,
                     0x4F: 0x61, 0x50: 0x62, 0x51: 0x63, 0x52: 0x60, 0x53: 0x6E}
MOUSE_NAMES = {"x1": "Mouse button 4 (back)", "x2": "Mouse button 5 (forward)", "middle": "Middle mouse button"}


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
user32.GetKeyNameTextW.argtypes = [ctypes.c_long, wt.LPWSTR, ctypes.c_int]
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE


def key_name(scan, ext, vk=0) -> str:
    buf = ctypes.create_unicode_buffer(64)
    if scan and user32.GetKeyNameTextW((scan << 16) | (int(bool(ext)) << 24), buf, 64):
        name = buf.value
        return name.replace("Num ", "Numpad ") if name.startswith("Num ") and len(name) <= 7 else name
    return f"Key {vk:#04x}"


def hotkey_from_key(vk, scan, flags) -> dict:
    ext = bool(flags & LLKHF_EXTENDED)
    if not ext and scan in NUMPAD_SCAN_TO_VK:
        vk = NUMPAD_SCAN_TO_VK[scan]                 # store the NumLock-on code
    return {"vk": int(vk), "scan": int(scan), "ext": ext, "name": key_name(scan, ext, vk)}


def hotkey_name(hk: dict) -> str:
    if not hk:
        return "(none)"
    if "mouse" in hk:
        return MOUSE_NAMES.get(hk["mouse"], hk["mouse"])
    return hk.get("name") or key_name(hk.get("scan", 0), hk.get("ext"), hk.get("vk", 0))


def key_matches(hk: dict, vk, scan, flags) -> bool:
    if not hk or "mouse" in hk:
        return False
    ext = bool(flags & LLKHF_EXTENDED)
    if scan and hk.get("scan"):
        return scan == hk["scan"] and ext == bool(hk.get("ext"))
    return vk == hk.get("vk")


# ---------------------------------------------------------------------------
# Tray icon
# ---------------------------------------------------------------------------
class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT),
                ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
                ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD), ("szInfo", wt.WCHAR * 256),
                ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wt.HICON)]


shell32 = ctypes.WinDLL("shell32", use_last_error=True)
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.LoadImageW.restype = wt.HANDLE
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ULONG_PTR, wt.LPCWSTR]
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, ctypes.c_void_p]
user32.TrackPopupMenu.restype = wt.BOOL
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
user32.RegisterWindowMessageW.restype = wt.UINT
user32.SetMenuDefaultItem.argtypes = [wt.HMENU, wt.UINT, wt.UINT]
NIM_ADD, NIM_MODIFY, NIM_DELETE, NIM_SETVERSION = 0, 1, 2, 4
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 1, 2, 4, 0x10
NIIF_INFO, NIIF_WARNING = 1, 2
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x10, 0x40
MF_STRING, MF_SEPARATOR, MF_GRAYED, MF_CHECKED = 0, 0x800, 1, 8
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 2, 0x100, 0x80
WM_LBUTTONUP, WM_RBUTTONUP, WM_LBUTTONDBLCLK, WM_CONTEXTMENU = 0x0202, 0x0205, 0x0203, 0x007B
WM_APP_TRAY, WM_APP_TRAYSTATE, WM_APP_NOTIFY, WM_APP_HOOKS = 0x8010, 0x8011, 0x8012, 0x8013


def load_icon(path):
    if not path or not os.path.isfile(path):
        return None
    return user32.LoadImageW(None, path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)


# ---------------------------------------------------------------------------
# One copy at a time, message boxes, start with Windows, the game's folder
# ---------------------------------------------------------------------------
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
kernel32.CreateMutexW.restype = wt.HANDLE
user32.MessageBoxW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT]
_mutex = None


def single_instance(name="Local\\PoEHarvestTracker") -> bool:
    """True if this is the only copy running."""
    global _mutex
    _mutex = kernel32.CreateMutexW(None, False, name)
    return ctypes.get_last_error() != 183           # ERROR_ALREADY_EXISTS


def release_single_instance():
    """Before restarting: let the new copy start."""
    global _mutex
    if _mutex:
        kernel32.CloseHandle(_mutex)
        _mutex = None


def message_box(text, title="PoE Harvest Tracker", warning=False, yes_no=False) -> bool:
    flags = (0x30 if warning else 0x40) | (0x4 if yes_no else 0) | 0x40000     # MB_TOPMOST
    return user32.MessageBoxW(None, text, title, flags) in (1, 6)              # IDOK / IDYES


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def set_start_with_windows(on: bool, name="PoE Harvest Tracker"):
    import sys
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on and getattr(sys, "frozen", False):
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, f'"{sys.executable}" --background')
        else:
            try:
                winreg.DeleteValue(k, name)
            except FileNotFoundError:
                pass


user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]


def poe_install_dir():
    """Folder of the running game's .exe (from its window's process; only the file path is read)."""
    hwnd = find_poe_window()
    if not hwnd:
        return None
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        buf, size = ctypes.create_unicode_buffer(1024), wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.dirname(buf.value)
    finally:
        kernel32.CloseHandle(h)
    return None


# ---------------------------------------------------------------------------
# Popup window: layered, click-through, never activated, always on top
# ---------------------------------------------------------------------------
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE),
                ("hIcon", wt.HICON), ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", wt.HDC), ("fErase", wt.BOOL), ("rcPaint", wt.RECT), ("fRestore", wt.BOOL),
                ("fIncUpdate", wt.BOOL), ("rgbReserved", ctypes.c_byte * 32)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = wt.ATOM
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.CreateWindowExW.restype = wt.HWND
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.SetLayeredWindowAttributes.argtypes = [wt.HWND, wt.COLORREF, wt.BYTE, wt.DWORD]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.InvalidateRect.argtypes = [wt.HWND, ctypes.c_void_p, wt.BOOL]
user32.UpdateWindow.argtypes = [wt.HWND]
user32.BeginPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.BeginPaint.restype = wt.HDC
user32.EndPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.SetTimer.argtypes = [wt.HWND, ULONG_PTR, wt.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ULONG_PTR
user32.KillTimer.argtypes = [wt.HWND, ULONG_PTR]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = wt.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.DestroyWindow.argtypes = [wt.HWND]
gdi32.SetDIBitsToDevice.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD, wt.DWORD, ctypes.c_int,
                                    ctypes.c_int, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.POINTER(BITMAPINFO),
                                    wt.UINT]
kernel32.GetCurrentThreadId.restype = wt.DWORD

WS_POPUP = 0x80000000
WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x8, 0x20, 0x80
WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x08000000
LWA_ALPHA = 2
SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x10, 0x40
SW_HIDE = 0
HWND_TOPMOST = wt.HWND(-1)
WM_PAINT, WM_ERASEBKGND, WM_TIMER, WM_MOUSEACTIVATE, WM_QUIT = 0x000F, 0x0014, 0x0113, 0x0021, 0x0012
MA_NOACTIVATE = 3
WM_APP_SHOW, WM_APP_HIDE = 0x8001, 0x8002
VK_BUTTONS = (0x01, 0x02, 0x04, 0x05, 0x06)       # left, right, middle, side buttons
POPUP_ALPHA = 240


class Ui:
    """One thread owns everything that needs a Windows message loop: the keyboard (and, when a
    hotkey is a mouse button, mouse) hook, the popup window and the tray icon."""

    def __init__(self, on_key, hotkeys=None, tray=None):
        self.on_key = on_key              # called (from the ui thread) as on_key(key, when, cursor, foreground)
        self.hotkeys = hotkeys or {}      # {"node": {...}, "result": {...}}
        self.tray = tray                  # {"icons": {state: path}, "menu": fn() -> items, "command": fn(id), "default": id}
        self.hwnd = None
        self.tray_hwnd = None
        self.thread_id = None
        self._pending = None              # (bytes, w, h, x, y) waiting to be shown
        self._bits = None
        self._size = (0, 0)
        self._visible = False
        self._shown_at = 0.0
        self._buttons_down = set()
        self._game = None                 # game window the popup belongs to
        self._held = {"node": False, "result": False}
        self._last_down = {"node": 0.0, "result": 0.0}
        self._capture = None              # callback while Settings waits for a new hotkey
        self._kb_hook = self._mouse_hook = None
        self._tray_state = ("ready", "PoE Harvest Tracker")
        self._icons = {}
        self._notify = None
        self._ready = threading.Event()
        self.error = None
        self._thread = threading.Thread(target=self._run, name="ui", daemon=True)

    # ---- called from other threads ----
    def start(self):
        self._thread.start()
        self._ready.wait(5)
        if self.error:
            raise RuntimeError(self.error)

    def show(self, image, x, y, game_hwnd):
        """image: PIL RGB image; x, y: screen position."""
        rgba = image.convert("RGBA")
        self._pending = (rgba.tobytes("raw", "BGRA"), rgba.width, rgba.height, int(x), int(y), game_hwnd)
        user32.PostMessageW(self.hwnd, WM_APP_SHOW, 0, 0)

    def hide(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP_HIDE, 0, 0)

    def set_hotkeys(self, hotkeys):
        self.hotkeys = hotkeys
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP_HOOKS, 0, 0)

    def capture_next_key(self, callback):
        """The next key or mouse side/middle button pressed goes to callback(hotkey dict) instead of
        reading the screen; Esc gives callback(None)."""
        self._capture = callback
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP_HOOKS, 0, 0)

    def set_tray(self, state, tip):
        self._tray_state = (state, tip)
        if self.tray_hwnd:
            user32.PostMessageW(self.tray_hwnd, WM_APP_TRAYSTATE, 0, 0)

    def notify(self, title, text, warning=False):
        """A Windows notification from the tray icon."""
        self._notify = (title, text, warning)
        if self.tray_hwnd:
            user32.PostMessageW(self.tray_hwnd, WM_APP_NOTIFY, 0, 0)

    def stop(self):
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)

    def join(self, timeout=None):
        self._thread.join(timeout)

    # ---- ui thread: hooks ----
    def _fire(self, key, down):
        now = time.monotonic()
        if down:
            gap = now - self._last_down[key]
            self._last_down[key] = now
            if not self._held[key] or gap > REPEAT_GAP:     # ignore auto-repeat
                self._held[key] = True
                self.on_key(key, dt.datetime.now(), cursor_pos(), foreground())
        else:
            self._held[key] = False

    def _finish_capture(self, hk):
        cb, self._capture = self._capture, None
        self._update_hooks()
        if cb:
            threading.Thread(target=cb, args=(hk,), daemon=True).start()

    def _kb(self, n_code, w_param, l_param):
        try:
            if n_code == HC_ACTION:
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
                if self._capture:
                    if down:
                        hk = None if kb.vkCode == VK_ESCAPE else hotkey_from_key(kb.vkCode, kb.scanCode, kb.flags)
                        self._finish_capture(hk)
                        return 1                    # swallow just this one key
                else:
                    for key, hk in self.hotkeys.items():
                        if key_matches(hk, kb.vkCode, kb.scanCode, kb.flags):
                            self._fire(key, down)
        except Exception:
            pass                                    # never block the keyboard
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _mouse(self, n_code, w_param, l_param):
        try:
            if n_code == HC_ACTION and w_param in (WM_XBUTTONDOWN, WM_XBUTTONUP, WM_MBUTTONDOWN, WM_MBUTTONUP):
                ms = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if w_param in (WM_MBUTTONDOWN, WM_MBUTTONUP):
                    button = "middle"
                else:
                    button = "x1" if (ms.mouseData >> 16) & 0xFFFF == 1 else "x2"
                down = w_param in (WM_XBUTTONDOWN, WM_MBUTTONDOWN)
                if self._capture:
                    if down:
                        self._finish_capture({"mouse": button, "name": MOUSE_NAMES[button]})
                else:
                    for key, hk in self.hotkeys.items():
                        if hk and hk.get("mouse") == button:
                            self._fire(key, down)
        except Exception:
            pass
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _update_hooks(self):
        """The mouse hook is only installed while it's needed (a mouse-button hotkey, or Settings
        waiting for a new hotkey)."""
        hinst = kernel32.GetModuleHandleW(None)
        need_mouse = bool(self._capture) or any(hk and "mouse" in hk for hk in self.hotkeys.values())
        if need_mouse and not self._mouse_hook:
            self._mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_ref, hinst, 0)
        elif not need_mouse and self._mouse_hook:
            user32.UnhookWindowsHookEx(self._mouse_hook)
            self._mouse_hook = None

    # ---- ui thread: popup ----
    def _wndproc(self, hwnd, msg, w_param, l_param):
        try:
            if msg == WM_APP_SHOW and self._pending:
                bits, w, h, x, y, game = self._pending
                self._pending = None
                self._bits, self._size, self._game = bits, (w, h), game
                user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE | SWP_SHOWWINDOW)
                user32.InvalidateRect(hwnd, None, False)
                user32.UpdateWindow(hwnd)
                self._visible, self._shown_at = True, time.monotonic()
                self._buttons_down = {b for b in VK_BUTTONS if user32.GetAsyncKeyState(b) & 0x8000}
                return 0
            if msg == WM_APP_HIDE:
                self._hide_now()
                return 0
            if msg == WM_APP_HOOKS:
                self._update_hooks()
                return 0
            if msg == WM_TIMER:
                self._poll()
                return 0
            if msg == WM_PAINT:
                ps = PAINTSTRUCT()
                hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
                if self._bits:
                    w, h = self._size
                    bmi = BITMAPINFO()
                    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                    bmi.bmiHeader.biWidth, bmi.bmiHeader.biHeight = w, -h      # top-down rows
                    bmi.bmiHeader.biPlanes, bmi.bmiHeader.biBitCount = 1, 32
                    gdi32.SetDIBitsToDevice(hdc, 0, 0, w, h, 0, 0, 0, h, self._bits, ctypes.byref(bmi), 0)
                user32.EndPaint(hwnd, ctypes.byref(ps))
                return 0
            if msg == WM_ERASEBKGND:
                return 1
            if msg == WM_MOUSEACTIVATE:
                return MA_NOACTIVATE
        except Exception:
            pass
        return user32.DefWindowProcW(hwnd, msg, w_param, l_param)

    def _poll(self):
        """While the popup is up: a new mouse click anywhere (it goes to the game as normal) or the
        game losing focus closes it - like Awakened PoE Trade."""
        if not self._visible:
            return
        down = {b for b in VK_BUTTONS if user32.GetAsyncKeyState(b) & 0x8000}
        new_click = bool(down - self._buttons_down)
        self._buttons_down = down
        settled = time.monotonic() - self._shown_at > 0.25
        lost_focus = self._game and foreground() not in (self._game, self.hwnd)
        if settled and (new_click or lost_focus):
            self._hide_now()

    def _hide_now(self):
        if self._visible:
            user32.ShowWindow(self.hwnd, SW_HIDE)
            self._visible = False

    # ---- ui thread: tray icon ----
    def _nid(self, flags):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd, nid.uID, nid.uFlags = self.tray_hwnd, 1, flags
        nid.uCallbackMessage = WM_APP_TRAY
        state, tip = self._tray_state
        nid.hIcon = self._icons.get(state) or self._icons.get("ready") or 0
        nid.szTip = tip[:127]
        return nid

    def _tray_add(self):
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)))

    def _tray_menu(self):
        items = self.tray["menu"]()
        menu = user32.CreatePopupMenu()
        for item in items:
            if item is None:
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                continue
            cmd, label, enabled, checked = item
            flags = MF_STRING | (0 if enabled else MF_GRAYED) | (MF_CHECKED if checked else 0)
            user32.AppendMenuW(menu, flags, cmd, label)
        if self.tray.get("default"):
            user32.SetMenuDefaultItem(menu, self.tray["default"], 0)
        x, y = cursor_pos()
        user32.SetForegroundWindow(self.tray_hwnd)            # so the menu closes when clicking elsewhere
        cmd = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY, x, y, 0, self.tray_hwnd, None)
        user32.DestroyMenu(menu)
        if cmd:
            threading.Thread(target=self.tray["command"], args=(cmd,), daemon=True).start()

    def _traywndproc(self, hwnd, msg, w_param, l_param):
        try:
            if msg == WM_APP_TRAY:
                event = l_param & 0xFFFF
                if event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._tray_menu()
                elif event == WM_LBUTTONDBLCLK and self.tray.get("default"):
                    threading.Thread(target=self.tray["command"], args=(self.tray["default"],), daemon=True).start()
                return 0
            if msg == WM_APP_TRAYSTATE:
                shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid(NIF_ICON | NIF_TIP)))
                return 0
            if msg == WM_APP_NOTIFY and self._notify:
                title, text, warning = self._notify
                nid = self._nid(NIF_INFO)
                nid.szInfoTitle, nid.szInfo = title[:63], text[:255]
                nid.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
                shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
                return 0
            if msg == self._taskbar_created:                   # Explorer restarted: put the icon back
                self._tray_add()
                return 0
        except Exception:
            pass
        return user32.DefWindowProcW(hwnd, msg, w_param, l_param)

    def _make_window(self, cls_name, proc, ex_style, style):
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = proc
        wc.hInstance = hinst
        wc.lpszClassName = cls_name
        user32.RegisterClassExW(ctypes.byref(wc))
        hwnd = user32.CreateWindowExW(ex_style, cls_name, "PoE Harvest Tracker", style, 0, 0, 10, 10,
                                      None, None, hinst, None)
        if not hwnd:
            raise OSError(f"couldn't create a window (error {ctypes.get_last_error()})")
        return hwnd

    def _run(self):
        try:
            self.thread_id = kernel32.GetCurrentThreadId()
            hinst = kernel32.GetModuleHandleW(None)
            self._wndproc_ref = WNDPROC(self._wndproc)          # keep references alive
            self._trayproc_ref = WNDPROC(self._traywndproc)
            self._kb_ref = HOOKPROC(self._kb)
            self._mouse_ref = HOOKPROC(self._mouse)
            ex = WS_EX_TOPMOST | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_NOACTIVATE
            self.hwnd = self._make_window("PoEHarvestPopup", self._wndproc_ref, ex, WS_POPUP)
            user32.SetLayeredWindowAttributes(self.hwnd, 0, POPUP_ALPHA, LWA_ALPHA)
            user32.SetTimer(self.hwnd, 1, 30, None)
            self._kb_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_ref, hinst, 0)
            if not self._kb_hook:
                raise OSError(f"couldn't listen for the hotkeys (error {ctypes.get_last_error()})")
            self._update_hooks()
            if self.tray:
                self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
                self.tray_hwnd = self._make_window("PoEHarvestTray", self._trayproc_ref, 0, 0)
                self._icons = {k: load_icon(v) for k, v in self.tray.get("icons", {}).items()}
                self._tray_add()
            else:
                self._taskbar_created = 0
        except Exception as exc:
            self.error = str(exc)
            self._ready.set()
            return
        self._ready.set()
        msg = wt.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self.tray_hwnd:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid(0)))
                user32.DestroyWindow(self.tray_hwnd)
            user32.UnhookWindowsHookEx(self._kb_hook)
            if self._mouse_hook:
                user32.UnhookWindowsHookEx(self._mouse_hook)
            user32.DestroyWindow(self.hwnd)


# ---------------------------------------------------------------------------
# Ctrl+C / closing the console window
# ---------------------------------------------------------------------------
CTRLHANDLER = ctypes.WINFUNCTYPE(wt.BOOL, wt.DWORD)
kernel32.SetConsoleCtrlHandler.argtypes = [CTRLHANDLER, wt.BOOL]


def on_console_close(callback):
    def handler(_event):
        try:
            callback()
        except Exception:
            pass
        return True
    ref = CTRLHANDLER(handler)
    kernel32.SetConsoleCtrlHandler(ref, True)
    return ref                                   # keep this alive


__all__ = ["set_dpi_awareness", "find_poe_window", "poe_region", "cursor_pos", "foreground", "play", "Ui",
           "window_title_starting", "on_console_close", "single_instance", "message_box", "set_start_with_windows",
           "poe_install_dir", "hotkey_name", "sound_settings"]
