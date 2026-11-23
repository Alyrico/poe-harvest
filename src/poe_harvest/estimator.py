"""
Everything the popup needs from the model: the estimate and its range, each lifeforce mod's effect
(no-effect mods marked as such), the yellow-wisp effect, Stacked Decks predicted from the mods, and
whether a reroll is worth 1 chaos at poe.ninja's prices.

A plant's value in chaos = lifeforce x its price + expected Stacked Decks x their price.
Stacked Decks = decks per Tier 3 Sacred Fauna (the mod) x Tier 3 fauna (your counted base + "Spawns N
additional Rare Tier 3") x 2 if Duplicated. Threads, Currency Items and Unique Items aren't valued.

Reroll advice: a reroll keeps the plant type and draws new mods. Every plant roll you've seen
(first rolls and rerolls) is a sample of what a fresh roll is worth. The best policy is "keep
rerolling while the plant is worth less than T chaos", where T is the value at which one more
reroll's expected improvement just pays for its chaos. Below T the popup says Reroll, and how much
the rerolling is worth on average (after paying for it).
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
DECK = "Stacked Deck"


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
        self.rolls = []
        self.icons = {}
        self.refit()

    # -------------------------------------------------------------------------------------------
    def setting(self, key, default=None):
        return self.settings.get(key, default) if self.settings else default

    def conditions(self) -> dict:
        """The map setup set now (stamped on new plants; fills in for plants from before)."""
        return {"atlas": self.setting("atlas_drops"), "tree": self.setting("tree_lifeforce") or 0,
                "white": bool(self.setting("white_map"))}

    def refit(self):
        """Fit on everything counted so far (a few ms). Called at start and after each result."""
        mode = (self.setting("starter_estimates") or "auto") if self.settings else "off"
        starter = mdl.load_starter() if mode != "off" else None
        defaults = self.conditions()
        defaults["white"] = False               # plants from before the white-map setting were normal maps
        self.model = mdl.choose(self.con, mode, defaults=defaults, starter=starter or {})
        rolls = mdl.rolls(self.con)
        if self.model is not None and self.model.mode == "starter" and starter:
            rolls += starter.get("rolls", [])
        self.rolls = rolls
        try:
            with open(os.path.join(self.data_dir, "mod_icons.json"), encoding="utf-8") as f:
                self.icons = json.load(f)
        except (OSError, ValueError):
            self.icons = {}
        if self.fetch_prices:
            self.prices.refresh_soon()

    @property
    def active(self) -> bool:
        return self.model is not None and self.model.usable

    def map_yellow(self, map_id):
        return (mdl.map_wisps(self.con).get(map_id) or {}).get("yellow")

    # -------------------------------------------------------------------------------------------
    def decks(self, plant: dict):
        """Expected Stacked Decks from this plant (None if it has no deck line, or if the Tier 3 base
        hasn't been counted yet)."""
        if DECK not in plant.get("rewards", {}):
            return None
        return mdl.reward_counts(plant, self.setting("t3_base")).get(DECK)

    def value(self, ptype, lifeforce, decks):
        """Chaos value of a plant (None without lifeforce prices)."""
        p_lf = self.prices.chaos_each(ptype)
        if not p_lf:
            return None
        return lifeforce * p_lf + (decks or 0) * (self.prices.chaos_each(DECK) or 0)

    def average_lifeforce(self, ptype, cond, wisp_mult=1.0) -> float:
        """What a fresh rare plant of this type gives on average here (over every roll you've seen)."""
        m = self.model
        parts = [m.mod_part(r) for r in self.rolls] or [0.0]
        return m.base(ptype, cond) * wisp_mult * float(np.mean(np.exp(parts)))

    def reroll_advice(self, ptype: str, plant: dict, cond: dict, wisp_mult: float):
        p_lf = self.prices.chaos_each(ptype)
        if not p_lf or len(self.rolls) < 20 or not self.active:
            return None
        m = self.model
        p_deck = self.prices.chaos_each(DECK) or 0
        scale = m.base(ptype, cond) * wisp_mult * math.exp(m.sigma ** 2 / 2)       # expected, not typical
        # every roll seen, valued as if it had come up on this plant
        X = np.array([scale * math.exp(m.mod_part(r)) * p_lf + (self.decks(r) or 0) * p_deck for r in self.rolls])
        E = scale * math.exp(m.mod_part(plant)) * p_lf + (self.decks(plant) or 0) * p_deck
        cost = CHAOS_PER_REROLL
        # T solves mean(max(X - T, 0)) = cost of one reroll (it's falling in T: bisect)
        if X.mean() <= cost:
            T = 0.0
        else:
            lo, hi = 0.0, float(X.max())
            for _ in range(60):
                mid = (lo + hi) / 2
                if np.maximum(X - mid, 0).mean() > cost:
                    lo = mid
                else:
                    hi = mid
            T = (lo + hi) / 2
        keep = E >= T
        return {"keep": keep, "gain_chaos": 0.0 if keep else T - E, "better_than": float((X < E).mean()),
                "target_share": float((X >= T).mean()), "target_chaos": T, "expected_chaos": E,
                "decks_counted": self.setting("t3_base") is not None}

    # -------------------------------------------------------------------------------------------
    def describe(self, ptype: str, mods: dict, rewards: dict, yellow, cond=None, rarity="rare") -> dict | None:
        """The node popup's estimate part, or None while there's no usable model."""
        if not self.active or ptype not in mdl.TYPES:
            return None
        m = self.model
        cond = dict(self.conditions(), **{k: v for k, v in (cond or {}).items() if v is not None})
        plant = {"type": ptype, "mods": mods, "rewards": rewards, "yellow": yellow or 0, **cond}
        est, lo, hi = m.estimate(plant)
        lines = []
        for mid, value in mods.items():
            eff = m.mod_effect(mid, value)
            colour = "reward" if mid in ("items_duplicated", "items_fully_linked", "items_max_sockets") else "mod"
            if eff is None:                             # no effect on lifeforce
                lines.append({"icon": self.icons.get(mid, "neutral") if self.icons.get(mid) in ("good", "bad") else "neutral",
                              "text": mod_text(mid, value), "colour": colour, "right": "no lifeforce", "effect": 0.0})
                continue
            e, elo, ehi = eff
            icon = m.icon(e, elo, ehi, self.icons.get(mid, "auto"))
            lines.append({"icon": icon, "text": mod_text(mid, value), "colour": colour, "right": pct(e), "effect": e})
        decks = self.decks(plant)
        p_deck = self.prices.chaos_each(DECK)
        for item, count in rewards.items():
            text = WORDING[item] if item in WORDING else mod_text("reward:" + item, count)
            if item == DECK and decks is not None:
                right = f"≈ {decks:g} decks" + (f" ≈ {decks * p_deck:.0f}c" if p_deck else "")
                lines.append({"icon": self.icons.get("reward:" + item, "good"), "text": text, "colour": "reward",
                              "right": right, "effect": 1.0 + (decks * (p_deck or 0)) / 100})
            elif item == DECK:
                lines.append({"icon": "neutral", "text": text, "colour": "reward",
                              "right": f"{count} per Tier 3 fauna", "effect": 0.5})
            else:
                lines.append({"icon": self.icons.get("reward:" + item, "neutral"), "text": text, "colour": "reward",
                              "right": "no lifeforce", "effect": 0.0})
        lines.sort(key=lambda l: -l["effect"])
        wisp_mult = 1.0
        if yellow:
            e, elo, ehi = m.wisp_effect(yellow)
            wisp_mult = 1 + e
            lines.append({"icon": m.icon(e, elo, ehi, self.icons.get("yellow_wisps", "auto")),
                          "text": f"Yellow wisps here: {yellow:,}", "colour": "wisps", "right": pct(e), "effect": e})
        avg = self.average_lifeforce(ptype, cond, wisp_mult)
        advice = self.reroll_advice(ptype, plant, cond, wisp_mult) if rarity == "rare" else None
        value = self.value(ptype, est, decks)
        return {"est": est, "lo": lo, "hi": hi, "average": avg, "vs_average": est / avg - 1,
                "decks": decks, "deck_line": DECK in rewards, "value": value,
                "lines": lines, "advice": advice, "rough": m.rough, "n": m.n, "mode": m.mode}
