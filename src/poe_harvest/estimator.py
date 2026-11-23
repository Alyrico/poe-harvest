"""
Everything the popup needs from the model (phase 4): the estimate and its range, each mod's effect
and icon, the yellow-wisp effect, and whether a reroll is worth 1 chaos at poe.ninja's prices.

Reroll advice: a reroll keeps the plant type and draws new mods. Every plant roll you've seen
(first rolls and rerolls) is a sample of what a fresh roll is worth. With a price per lifeforce, the
best policy is "keep rerolling while the plant is worth less than T", where T is the value at which
one more reroll's expected improvement just pays for its chaos. If the current plant is below T,
the popup says Reroll and how much the rerolling is worth on average, in chaos (after paying for it).
"""
from __future__ import annotations

import json
import math
import os

import numpy as np

from . import model as mdl
from . import mods as modlib
from .prices import Prices

WORDING = {mid: wording for mid, wording, _ in modlib.MODS}
CHAOS_PER_REROLL = 1


def mod_text(mid: str, value) -> str:
    if mid.startswith("reward:"):
        item = mid.split(":", 1)[1]
        return f"Tier 3 drop: {value} {item}" if value and value > 1 else f"Tier 3 drop: {item}"
    w = WORDING.get(mid, mid)
    return w.replace("#", f"{value:,}" if isinstance(value, int) else "?")


def pct(x: float) -> str:
    return f"{x * 100:+.0f}%"


class Estimator:
    def __init__(self, con, data_dir: str, fetch_prices: bool = True, settings=None):
        self.con = con
        self.data_dir = data_dir
        self.settings = settings
        self.prices = Prices(data_dir, league=lambda: (settings.get("league") if settings else ""))
        self.fetch_prices = fetch_prices
        self.model = None
        self.roll_parts = []
        self.icons = {}
        self.refit()

    # -------------------------------------------------------------------------------------------
    def refit(self):
        """Fit on everything counted so far (a few ms). Called at start and after each result."""
        mode = self.settings.get("starter_estimates", "auto") if self.settings else "off"
        starter = mdl.load_starter() if mode != "off" else None
        self.model = mdl.choose(self.con, mode, default_atlas=self.atlas(), starter=starter or {})
        rolls = mdl.rolls(self.con)
        if self.model is not None and self.model.mode == "starter" and starter:
            rolls += starter.get("rolls", [])
        self.roll_parts = [self.model.mod_part(r) for r in rolls] if self.model else []
        try:
            with open(os.path.join(self.data_dir, "mod_icons.json"), encoding="utf-8") as f:
                self.icons = json.load(f)
        except (OSError, ValueError):
            self.icons = {}
        if self.fetch_prices:
            self.prices.refresh_soon()

    def atlas(self):
        """The Atlas Drops % set now (None if never set)."""
        return self.settings.get("atlas_drops") if self.settings else None

    @property
    def active(self) -> bool:
        return self.model is not None and self.model.usable

    def map_yellow(self, map_id):
        return (mdl.map_wisps(self.con).get(map_id) or {}).get("yellow")

    # -------------------------------------------------------------------------------------------
    def reroll_advice(self, ptype: str, plant_part: float, wisp_mult: float, atlas=None):
        price = self.prices.chaos_each(ptype)
        if not price or len(self.roll_parts) < 20 or not self.active:
            return None
        m = self.model
        lf_per_chaos = CHAOS_PER_REROLL / price             # lifeforce one reroll costs
        scale = m.typical(ptype, atlas) * wisp_mult * math.exp(m.sigma ** 2 / 2)   # expected, not typical
        X = scale * np.exp(np.array(self.roll_parts))
        E = scale * math.exp(plant_part)
        # T solves mean(max(X - T, 0)) = cost of one reroll (it's falling in T: bisect)
        if X.mean() <= lf_per_chaos:
            T = 0.0
        else:
            lo, hi = 0.0, float(X.max())
            for _ in range(60):
                mid = (lo + hi) / 2
                if np.maximum(X - mid, 0).mean() > lf_per_chaos:
                    lo = mid
                else:
                    hi = mid
            T = (lo + hi) / 2
        better_than = float((X < E).mean())
        keep = E >= T
        return {"keep": keep, "gain_chaos": 0.0 if keep else (T - E) * price, "better_than": better_than,
                "target_share": float((X >= T).mean()),          # share of rolls good enough to keep
                "target": T / math.exp(m.sigma ** 2 / 2),        # on the same (typical) scale as the estimate
                "threshold": T, "expected": E, "price": price}

    # -------------------------------------------------------------------------------------------
    def describe(self, ptype: str, mods: dict, rewards: dict, yellow, atlas=None) -> dict | None:
        """The node popup's estimate part, or None while there's no usable model."""
        if not self.active or ptype not in mdl.TYPES:
            return None
        m = self.model
        atlas = self.atlas() if atlas is None else atlas
        plant = {"type": ptype, "mods": mods, "rewards": rewards, "yellow": yellow or 0, "atlas": atlas}
        est, lo, hi = m.estimate(plant)
        typical = m.typical(ptype, atlas)
        lines = []
        for mid, value in mods.items():
            if mid in mdl.MOD_IDS:
                e, elo, ehi = m.mod_effect(mid, value)
            else:                                       # fully linked / max sockets
                e, elo, ehi = m.mod_effect(mdl.REWARD)
            icon = m.icon(e, elo, ehi, self.icons.get(mid, "auto"))
            colour = "reward" if mid in ("items_duplicated", "items_fully_linked", "items_max_sockets") else "mod"
            lines.append({"icon": icon, "text": mod_text(mid, value), "colour": colour, "right": pct(e), "effect": e})
        for item, count in rewards.items():
            e, elo, ehi = m.mod_effect(mdl.REWARD)
            icon = m.icon(e, elo, ehi, self.icons.get("reward:" + item, self.icons.get(mdl.REWARD, "auto")))
            text = WORDING[item] if item in WORDING else mod_text("reward:" + item, count)
            lines.append({"icon": icon, "text": text, "colour": "reward",
                          "right": pct(e), "effect": e})
        lines.sort(key=lambda l: -l["effect"])
        wisp_mult = 1.0
        if yellow:
            e, elo, ehi = m.wisp_effect(yellow)
            wisp_mult = 1 + e
            lines.append({"icon": m.icon(e, elo, ehi, self.icons.get("yellow_wisps", "auto")),
                          "text": f"Yellow wisps here: {yellow:,}", "colour": "wisps", "right": pct(e), "effect": e})
        advice = self.reroll_advice(ptype, m.mod_part(plant), wisp_mult, atlas)
        return {"est": est, "lo": lo, "hi": hi, "typical": typical, "vs_typical": est / typical - 1,
                "lines": lines, "advice": advice, "rough": m.rough, "n": m.n, "mode": m.mode}
