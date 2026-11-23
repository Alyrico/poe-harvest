"""
Lifeforce prices from poe.ninja (currency exchange), for the reroll advice.

  * Fetched at most once an hour, in the background - a key press never waits for the internet.
  * Kept in data\\prices.json, so the last known prices are used when offline.
  * League: poe.ninja's current challenge league (softcore) unless one is picked in Settings.

Endpoints (https://poe.ninja/docs/api):
  /poe1/api/economy/leagues                                   -> [{"id": "Covens", ...}, ...]
  /poe1/api/economy/exchange/current/overview?league=..&type=Currency
      lines[]: {"id": "wild-lifeforce", "primaryValue": 0.0214 (chaos each), ...}
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request

BASE_URL = "https://poe.ninja/poe1/api/economy"
from . import __version__  # noqa: E402

USER_AGENT = f"PoE-Harvest-Tracker/{__version__} (+https://github.com/Alyrico/poe-harvest; about one request an hour)"
MAX_AGE = 3600                       # seconds
IDS = {"Wild": "wild-lifeforce", "Vivid": "vivid-lifeforce", "Primal": "primal-lifeforce",
       "Sacred": "sacred-lifeforce", "Stacked Deck": "stacked-deck"}


def _get(url: str, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class Prices:
    def __init__(self, data_dir: str, league=None):
        self.path = os.path.join(data_dir, "prices.json")
        self.settings_path = os.path.join(data_dir, "settings.json")
        self.league = league                 # a function giving the chosen league ("" = current)
        self.cache = _load(self.path)
        self._busy = threading.Lock()
        self.error = ""

    # chaos per lifeforce, or None
    def chaos_each(self, colour: str):
        return (self.cache.get("prices") or {}).get(colour)

    def info(self) -> dict:
        return {"league": self.cache.get("league"), "fetched": self.cache.get("fetched"),
                "prices": self.cache.get("prices") or {}, "error": self.error}

    def stale(self) -> bool:
        wanted = self.league() if callable(self.league) else self.league
        if wanted and wanted != self.cache.get("league"):
            return True                       # a different league was picked
        return time.time() - self.cache.get("fetched_ts", 0) > MAX_AGE

    def refresh_soon(self):
        """Start a background fetch if the prices are over an hour old (returns at once)."""
        if self.stale() and not self._busy.locked():
            threading.Thread(target=self.fetch, daemon=True).start()

    def fetch(self):
        if not self._busy.acquire(blocking=False):
            return
        try:
            league = (self.league() if callable(self.league) else self.league) or _load(self.settings_path).get("league")
            if not league:
                leagues = _get(f"{BASE_URL}/leagues")
                league = leagues[0]["id"]           # first entry = current challenge league
            data = _get(f"{BASE_URL}/exchange/current/overview?"
                        + urllib.parse.urlencode({"league": league, "type": "Currency"}))
            by_id = {line.get("id"): line.get("primaryValue") for line in data.get("lines", [])}
            prices = {colour: by_id[i] for colour, i in IDS.items() if by_id.get(i)}
            if not prices:
                raise ValueError("no lifeforce prices in the response")
            self.cache = {"league": league, "prices": prices, "fetched_ts": time.time(),
                          "fetched": time.strftime("%Y-%m-%d %H:%M")}
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=1)
            os.replace(tmp, self.path)
            self.error = ""
        except Exception as exc:                      # offline, site changed... keep the old prices
            self.error = f"{type(exc).__name__}: {exc}"
            self.cache["fetched_ts"] = time.time() - MAX_AGE + 600     # try again in 10 minutes
        finally:
            self._busy.release()
