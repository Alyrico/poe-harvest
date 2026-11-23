"""Which area you're in, from the game's log file (reading it is allowed by GGG)."""
from __future__ import annotations

import os
import re
import time

LOG_NAMES = ("LatestClient.txt", "Client.txt")
STANDARD_GAME_DIRS = [
    r"C:\Program Files (x86)\Grinding Gear Games\Path of Exile",
    r"C:\Program Files\Grinding Gear Games\Path of Exile",
    r"C:\Program Files (x86)\Steam\steamapps\common\Path of Exile",
    r"C:\Program Files\Epic Games\PathOfExile",
    r"D:\GGG",
]


def _steam_dirs():
    """Path of Exile in every Steam library (from Steam's own library list)."""
    out = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam = winreg.QueryValueEx(k, "SteamPath")[0]
    except Exception:
        return out
    libs = [steam]
    try:
        with open(os.path.join(steam, "steamapps", "libraryfolders.vdf"), encoding="utf-8", errors="replace") as f:
            libs += [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', f.read())]
    except OSError:
        pass
    for lib in libs:
        out.append(os.path.join(lib, "steamapps", "common", "Path of Exile"))
    return out


def _running_game_dir():
    """The folder of the running Path of Exile (from its window's process - just the file path)."""
    try:
        from . import win
        return win.poe_install_dir()
    except Exception:
        return None


def find_game_log(setting: str = "") -> str | None:
    """The log to follow: the setting if it's a file, else the running game's, else a standard place.
    In a folder with both logs, the one written most recently."""
    if setting and os.path.isfile(setting):
        return setting
    dirs = []
    if setting and os.path.isdir(setting):
        dirs.append(setting)
    running = _running_game_dir()
    if running:
        dirs.append(running)
    dirs += STANDARD_GAME_DIRS + _steam_dirs()
    for d in dirs:
        logs = [os.path.join(d, "logs", n) for n in LOG_NAMES if os.path.isfile(os.path.join(d, "logs", n))]
        if logs:
            return max(logs, key=lambda p: os.path.getmtime(p))
    return None
MAP_PREFIX = "Map"                 # map areas are logged as e.g. "MapWorldsMausoleum"
AREA_RE = re.compile(rb'Generating level (\d+) area "([^"]+)" with seed (\d+)')
FIRST_READ_BYTES = 16 * 1024 * 1024


def in_map(area_id: str | None) -> bool:
    return bool(area_id) and area_id.startswith(MAP_PREFIX)


def pretty(area_id: str | None) -> str:
    if not area_id:
        return "?"
    name = re.sub(r"^MapWorlds|^Map", "", area_id).rstrip("_")      # e.g. MapWorldsColdRiver_
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)


class ZoneTracker:
    """Reads only the new part of the log each time it's asked. If the log isn't found (game not
    started yet), it looks again at most every 20 seconds."""

    def __init__(self, setting: str = ""):
        self.setting = setting
        self.path = find_game_log(setting)
        self._looked = time.monotonic()
        self.offset = None
        self.created = None
        self.area = None      # e.g. "MapWorldsMausoleum"
        self.seed = None      # changes for every new instance of an area
        self.level = None     # the area's level (83 = tier 16 map)

    def set_path(self, setting: str):
        self.setting, self.path, self.offset = setting, find_game_log(setting), None
        self._looked = time.monotonic()

    def current(self) -> tuple[str | None, str | None]:
        if self.path is None and time.monotonic() - self._looked > 20:
            self.path, self._looked = find_game_log(self.setting), time.monotonic()
        if self.path is None:
            return None, None
        try:
            st = os.stat(self.path)
            created = getattr(st, "st_birthtime", st.st_ctime)
            size = st.st_size
            if self.offset is None or size < self.offset or created != self.created:
                self.created, self.area, self.seed, self.level = created, None, None, None
                start = max(0, size - FIRST_READ_BYTES)
            else:
                start = self.offset
            if size > start:
                with open(self.path, "rb") as f:
                    f.seek(start)
                    data = f.read(size - start)
                end = data.rfind(b"\n") + 1          # only complete lines
                found = AREA_RE.findall(data[:end])
                if found:
                    self.level = int(found[-1][0])
                    self.area = found[-1][1].decode("utf-8", "replace")
                    self.seed = found[-1][2].decode()
                self.offset = start + end
            else:
                self.offset = start
        except OSError:
            pass
        return self.area, self.seed
