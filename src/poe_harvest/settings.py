"""
Settings and folders.

    %APPDATA%\\PoE Harvest\\settings.json   the settings (small; always here)
    <data folder>\\                          everything the app collects (default Documents\\PoE Harvest):
        harvest.db            the database
        manual_fixes.json     corrections made in the dashboard (a rebuild keeps them)
        mod_icons.json        icon choices from the dashboard
        prices.json           last lifeforce prices from poe.ninja
        encounters.csv        the database as a spreadsheet
        reads\\<date>\\         cut-outs of what was read (always kept, small)
        screenshots\\          full screenshots (only if "Save full screenshots" is on)
        logs\\app.log          what the app did, for when something goes wrong
"""
from __future__ import annotations

import copy
import json
import os
import threading

APP_NAME = "PoE Harvest Tracker"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "PoE Harvest")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")

# A hotkey is the physical key: its scan code + "extended" flag (the same with NumLock on or off),
# plus the virtual-key code for programs (e.g. mouse software) that send keys without a scan code.
# Mouse buttons: {"mouse": "x1" | "x2" | "middle"}.
NUMPAD_1 = {"vk": 0x61, "scan": 0x4F, "ext": False, "name": "Numpad 1"}
NUMPAD_3 = {"vk": 0x63, "scan": 0x51, "ext": False, "name": "Numpad 3"}

DEFAULTS = {
    "data_dir": "",                 # "" = Documents\PoE Harvest
    "screenshots_dir": "",          # "" = <data folder>\screenshots
    "atlas_drops": None,            # the map's Atlas Drops %; asked on first start
    "hotkeys": {"node": NUMPAD_1, "result": NUMPAD_3},
    "poe_log": "",                  # "" = find Client.txt automatically (standalone or Steam)
    "rich_soil": None,              # Rich Soil allocated (mods x1.5); asked on first start
    "sounds": True,
    "volume": 70,                   # 0-100
    "popup_scale": 1.0,
    "save_screenshots": False,      # full screenshots are ~6 MB each; cut-outs are always kept
    "keep_screenshots_days": 30,    # 0 = keep forever
    "league": "",                   # "" = poe.ninja's current challenge league
    "label_colour": [255, 0, 255],  # the loot filter's lifeforce label background (RGB)
    "check_updates": True,
    "start_with_windows": False,
    "port": 8765,
    "starter_estimates": "auto",    # "auto" / "on" / "off": estimates from the author's data until you have your own
    "first_run_done": False,
}


def documents_dir() -> str:
    """The user's Documents folder (it can be moved, e.g. to OneDrive)."""
    try:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        # CSIDL_PERSONAL = 5
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
            return buf.value
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Documents")


class Settings:
    """Thread-safe settings: read with s["key"] / s.get(), change with s.update(...) (saved at once)."""

    def __init__(self, path: str = SETTINGS_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._listeners = []
        self.data = copy.deepcopy(DEFAULTS)
        try:
            with open(path, encoding="utf-8") as f:
                stored = json.load(f)
            for k, v in stored.items():
                if k in DEFAULTS:
                    self.data[k] = v
        except (OSError, ValueError):
            pass

    def __getitem__(self, key):
        with self._lock:
            return copy.deepcopy(self.data[key])

    def get(self, key, default=None):
        with self._lock:
            return copy.deepcopy(self.data.get(key, default))

    def update(self, **changes):
        with self._lock:
            for k, v in changes.items():
                if k not in DEFAULTS:
                    raise KeyError(k)
                self.data[k] = v
            self.save_locked()
        for cb in list(self._listeners):
            try:
                cb(changes)
            except Exception:
                pass

    def on_change(self, callback):
        self._listeners.append(callback)

    def save_locked(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=1)
        os.replace(tmp, self.path)

    # ---- folders ----
    @property
    def data_dir(self) -> str:
        d = self.get("data_dir") or os.path.join(documents_dir(), "PoE Harvest")
        return os.path.abspath(d)

    @property
    def screenshots_dir(self) -> str:
        return os.path.abspath(self.get("screenshots_dir") or os.path.join(self.data_dir, "screenshots"))

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "harvest.db")

    @property
    def reads_dir(self) -> str:
        return os.path.join(self.data_dir, "reads")

    @property
    def logs_dir(self) -> str:
        return os.path.join(self.data_dir, "logs")

    def ensure_dirs(self):
        for d in (self.data_dir, self.reads_dir, self.logs_dir):
            os.makedirs(d, exist_ok=True)

    @property
    def flora_mod_effect(self) -> float:
        """Rich Soil allocated: Sacred Flora mods have 50% increased effect."""
        return 1.5 if self.get("rich_soil") in (True, None) else 1.0


# one shared instance for the running app
_current: Settings | None = None


def current() -> Settings:
    global _current
    if _current is None:
        _current = Settings()
    return _current


def use(settings: Settings):
    """Tests and tools can swap in their own settings."""
    global _current
    _current = settings
