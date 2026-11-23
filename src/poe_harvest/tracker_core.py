"""
Turns key presses into encounters (no Windows code here, so it can be tested by replaying
screenshots).

Rules (from PLAN.md):
  * Numpad 1 reads the plant. A plant is always finished (Numpad 3) before another of the same
    type in the same map is read (agreed 22 Nov), so another read of the same type in the same
    map with no result since is the same plant: changed mods = a reroll (1 chaos), same mods =
    the same roll again. However long it took and wherever you stood. A reroll also changes the
    plant's name, so the name isn't used.
  * Numpad 3 reads the lifeforce and completes the encounter. Pressing again within two minutes
    (before reading another plant) replaces the amounts.
  * A plant that never gets a result (another type read, map left, tracker closed) is
    "dropped": left out of everything - statistics, review, rerolls, reroll advice (agreed 22 Nov).
  * Lifeforce with no plant read before it -> stored as "no node", low buzz.
  * Anything read badly -> "needs review" / "unreadable", low buzz, excluded from statistics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from . import db as dblib
from . import mods as modlib
from . import zones

RESULT_REPLACE_SECONDS = 120
CHAOS_PER_REROLL = 1
from .model import ROUGH_UNTIL, SHOW_FROM  # noqa: E402  (estimates: shown from / rough until)

WORDING = {mid: wording for mid, wording, _ in modlib.MODS}


@dataclass
class NodeEvent:
    time: datetime
    area: str | None
    instance: str | None
    parsed: modlib.ParsedTooltip
    wisps: dict
    box: tuple                 # tooltip x0, y0, x1, bottom (screen pixels inside the game window)
    screenshot: str = ""
    crop: str = ""
    cond: dict | None = None    # the map setup at the time: {"atlas", "tree", "white", "level"}


@dataclass
class ResultEvent:
    time: datetime
    area: str | None
    instance: str | None
    stacks: list               # [(amount or None, colour or None), ...]
    wisps: dict
    box: tuple | None          # bounding box of all labels
    screenshot: str = ""
    crop: str = ""


@dataclass
class Outcome:
    sound: str | None          # "node", "result", "buzz" or None (stay silent)
    popup: dict | None
    log: str
    encounter_id: int | None = None
    extra: dict = field(default_factory=dict)


def mod_text(mid: str, value) -> str:
    if mid.startswith("reward:"):
        item = mid.split(":", 1)[1]
        return f"Tier 3 drop: {value} {item}" if value and value > 1 else f"Tier 3 drop: {item}"
    w = WORDING.get(mid, mid)
    return w.replace("#", f"{value:,}" if isinstance(value, int) else "?")


class EncounterTracker:
    def __init__(self, con, source: str = "live", estimator=None):
        self.con = con
        self.source = source
        self.estimator = estimator  # phase 4: estimates in the popup (None when replaying screenshots)
        self.pending = None        # dict for the plant read but not finished
        self.last_done = None      # dict for the encounter finished most recently
        con.execute("UPDATE encounters SET status = 'dropped' WHERE status = 'pending'")
        con.commit()

    # ------------------------------------------------------------------ helpers
    def _map(self, ev) -> int:
        name = zones.pretty(ev.area)
        return dblib.map_id(self.con, ev.area or "", ev.instance or "", name, ev.time.isoformat(timespec="seconds"))

    def _drop_pending(self):
        """The plant read never got a result: leave it out of everything."""
        if self.pending:
            self.con.execute("UPDATE encounters SET status = 'dropped', note = 'no Numpad 3 result' WHERE id = ?",
                             (self.pending["id"],))
            self.pending = None

    def _close_if_left_map(self, map_id: int):
        if self.pending and self.pending["map_id"] != map_id:
            self._drop_pending()
        if self.last_done and self.last_done["map_id"] != map_id:
            self.last_done = None

    def _footer(self) -> str:
        n = dblib.count_ok(self.con)
        text = f"{n} encounter{'s' if n != 1 else ''} logged"
        est = self.estimator
        if est is None:
            return text
        if est.active and est.model.mode == "starter":
            return text + f" · starter estimates (from {est.model.n_starter} of the author's plants) until you have {ROUGH_UNTIL}"
        if est.active:
            return text + (f" · estimates are rough until {ROUGH_UNTIL}" if est.model.rough else "")
        if n < SHOW_FROM:
            return text + f" · estimates from ~{SHOW_FROM}"
        return text + " · estimates once they beat the type average"

    # ------------------------------------------------------------------ Numpad 1
    def node(self, ev: NodeEvent) -> Outcome:
        p = ev.parsed
        mods = p.mod_values()
        problems = list(p.problems) + [f"not recognised: {u}" for u in p.unknown]
        if p.kind == "flora" and not mods:
            problems.append("no mods read")
        clean = not problems
        map_id = self._map(ev)
        self._close_if_left_map(map_id)
        when = ev.time.isoformat(timespec="seconds")
        centre = (ev.box[0] + ev.box[2]) / 2, ev.box[1]
        w = ev.wisps

        reroll = repeat = False
        pend = self.pending
        # same map (checked above), same kind and type, no result since -> the same plant.
        # It's a new roll only if both reads are clean and the mods really changed: a different set
        # of mods, or different numbers AND a different name (same mods + same name = OCR noise).
        if pend and pend["kind"] == p.kind and pend["colour"] == p.colour:
            both_clean = clean and pend.get("clean")
            same_lines = set(mods) == set(pend["mods"]) and set(p.rewards) == set(pend["rewards"])
            same_values = mods == pend["mods"] and p.rewards == pend["rewards"]
            if both_clean and not same_lines:
                reroll = True
            elif both_clean and not same_values and (p.name or "") != (pend.get("name") or ""):
                reroll = True
            else:
                repeat = True             # the same roll read again (or a read that failed)
        if pend and (reroll or repeat):
            eid = pend["id"]
            if reroll:
                pend["rerolls"] += 1
            elif clean and not (pend.get("first") or {}).get("clean") and not pend["rerolls"]:
                # the first read failed and this one is good: it's the real first roll
                pend["first"] = {"mods": mods, "rewards": dict(p.rewards), "clean": True, "yellow": w.get("yellow", 0)}
        else:
            cond = ev.cond or {}
            self._drop_pending()      # a different plant: the earlier one never got a result
            cur = self.con.execute(
                "INSERT INTO encounters (map_id, kind, status, source, node_time, atlas_drops, tree_lifeforce, "
                "white_map, area_level) VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?)",
                (map_id, p.kind, self.source, when, cond.get("atlas"), cond.get("tree"),
                 None if cond.get("white") is None else int(bool(cond.get("white"))), cond.get("level")))
            eid = cur.lastrowid
            pend = self.pending = {"id": eid, "map_id": map_id, "kind": p.kind, "colour": p.colour, "cond": cond,
                                   "rerolls": 0, "first": {"mods": mods, "rewards": dict(p.rewards), "clean": clean,
                                                           "yellow": w.get("yellow", 0)}}
        # a failed re-read of a roll that was already read cleanly doesn't overwrite the good read
        keep_good = repeat and not clean and pend.get("clean")
        if keep_good:
            pend.update(time=ev.time)
        else:
            pend.update(time=ev.time, centre=centre, mods=mods, rewards=dict(p.rewards), clean=clean,
                        name=p.name)
            self.con.execute(
                "UPDATE encounters SET plant_type = ?, rarity = ?, plant_name = ?, node_time = ?, rerolls = ?, "
                "chaos_spent = ?, wisps_yellow_node = ?, wisps_blue_node = ?, wisps_purple_node = ? WHERE id = ?",
                (p.colour, p.rarity, p.name, when, pend["rerolls"], pend["rerolls"] * CHAOS_PER_REROLL,
                 w.get("yellow", 0), w.get("blue", 0), w.get("purple", 0), eid))
            dblib.set_mods(self.con, eid, mods, p.rewards)
        dblib.add_read(self.con, eid, "node", when, ev.screenshot, ev.crop,
                       {"name": p.name, "type": p.colour, "rarity": p.rarity, "mods": mods,
                        "rewards": p.rewards, "unknown": p.unknown, "problems": p.problems, "wisps": w}, clean)
        self.con.commit()
        self.last_done = None

        # popup: the estimate (phase 4) or, before there's enough data, what was read
        desc = None
        if self.estimator is not None and clean and p.kind == "flora":
            yellow = w.get("yellow", 0) if w.get("yellow", 0) is not None else self.estimator.map_yellow(map_id)
            desc = self.estimator.describe(p.colour, mods, p.rewards, yellow, pend.get("cond"), p.rarity)
        if desc:
            prev = pend.get("est") if (reroll or repeat) else None
            pend["est"] = desc["est"]
            if not pend.get("est_first"):
                pend["est_first"] = desc["est"] if not (reroll or repeat) else self._first_estimate(pend)
            self.con.execute("UPDATE encounters SET est = ?, est_lo = ?, est_hi = ?, est_first = ? WHERE id = ?",
                             (desc["est"], desc["lo"], desc["hi"], pend.get("est_first"), eid))
            self.con.commit()
            popup = self._estimate_popup(p, desc, w, ev, pend, reroll, repeat, prev)
            what = "reroll" if reroll else "repeat" if repeat else "node"
            return Outcome("node", popup, f"{what:6} {p.name} ({p.colour}) {mods} {p.rewards}  est {desc['est']:.0f}",
                           eid, {"reroll": reroll, "repeat": repeat})
        lines = []
        for m in p.mods:
            if m.kind == "mod":
                lines.append((mod_text(m.id, m.value), "reward" if m.id == "items_duplicated" else "mod"))
        for item, count in p.rewards.items():
            text = WORDING[item] if item in WORDING else mod_text("reward:" + item, count)
            lines.append((text, "reward"))
        for u in p.unknown:
            lines.append(("? " + u.capitalize(), "bad"))
        for prob in p.problems:
            lines.append(("! " + prob, "bad"))
        if p.kind == "flora" and not mods:
            lines.append(("! no mods read - press Numpad 1 again", "bad"))
        if w:          # a count that couldn't be read is shown as "?" and stored as unknown
            lines.append(("Wisps here: " + " · ".join(f"{v:,} {k}" if v is not None else f"? {k}"
                                                      for k, v in w.items()), "wisps"))
        status = ""
        if reroll:
            status = f"Reroll {pend['rerolls']} ({pend['rerolls'] * CHAOS_PER_REROLL} chaos)"
        elif repeat:
            status = "Same as before"
        title_right = f"{p.colour or '?'} · {p.rarity.capitalize()}" if p.kind == "flora" else "Specimen"
        popup = {"kind": "node", "title": p.name or "?", "title_right": title_right, "type": p.colour,
                 "lines": lines, "status": status, "footer": self._footer(), "anchor": ev.box}
        what = "reroll" if reroll else "repeat" if repeat else "node"
        return Outcome("node" if clean else "buzz", popup,
                       f"{what:6} {p.name} ({p.colour}) {mods} {p.rewards}" + (f"  PROBLEMS {problems}" if problems else ""),
                       eid, {"reroll": reroll, "repeat": repeat})

    def _first_estimate(self, pend):
        """Estimate of the plant's first roll with today's model (None if it can't be estimated)."""
        f = pend.get("first") or {}
        if self.estimator is None or not f.get("clean") or not self.estimator.active:
            return None
        d = self.estimator.describe(pend["colour"], f["mods"], f["rewards"], f.get("yellow") or 0, pend.get("cond"))
        return d["est"] if d else None

    def _estimate_popup(self, p, desc, w, ev, pend, reroll, repeat, prev) -> dict:
        from .popup_render import TYPE_COLOURS
        head = f"≈ {desc['est']:,.0f} {p.colour} lifeforce"
        if desc.get("decks"):
            head += f"  +  ≈ {desc['decks']:g} Stacked Decks"
        lines = [{"text": head + ("  (rough)" if desc["rough"] else ""), "big": True, "rgb": TYPE_COLOURS.get(p.colour)},
                 {"text": f"likely {desc['lo']:,.0f}–{desc['hi']:,.0f}  ·  {desc['vs_average'] * 100:+.0f}% vs an average "
                          f"{p.colour} plant" + (f"  ·  worth ≈ {desc['value']:,.0f}c" if desc.get("value") else ""),
                  "small": True, "colour": "sub"}]
        lines += desc["lines"]
        if desc.get("deck_line") and desc.get("decks") is None:
            lines.append({"text": "Count the Tier 3 fauna on a plant and enter it in Settings to value the decks",
                          "colour": "sub", "small": True})
        others = [f"{v:,} {k}" if v is not None else f"? {k}" for k, v in w.items() if k != "yellow"]
        if w.get("yellow", 0) is None:
            others.insert(0, "? yellow")
        if others:
            lines.append({"text": "Other wisps here: " + " · ".join(others), "colour": "wisps", "small": True})
        a = desc["advice"]
        if a:
            if a["keep"]:
                lines.append({"icon": "good", "text": f"Keep - better than {a['better_than'] * 100:.0f}% of rolls",
                              "colour": "good"})
            elif a["gain_chaos"] < 1:
                lines.append({"icon": "neutral", "text": "Close call - keep, or reroll for a small gain",
                              "colour": "neutral", "right": f"worth ≈ {a['gain_chaos']:+.1f}c"})
            else:
                lines.append({"icon": "neutral", "text": f"Reroll - aim for a plant worth {a['target_chaos']:,.0f}c+ "
                                                          f"(top {max(a['target_share'] * 100, 1):.0f}% of rolls)",
                              "colour": "neutral", "right": f"worth ≈ {a['gain_chaos']:+.1f}c"})
        status = ""
        if reroll:
            spent = pend["rerolls"] * CHAOS_PER_REROLL
            status = f"Reroll {pend['rerolls']} ({spent} chaos)"
            if prev:
                status += f"  ·  vs last roll: {desc['est'] - prev:+,.0f}"
            first = pend.get("est_first")
            price = self.estimator.prices.chaos_each(p.colour) if self.estimator else None
            if first and pend["rerolls"] > 1:
                gain = desc["est"] - first
                status += f"  ·  vs first roll: {gain:+,.0f}"
                if price:
                    status += f" ≈ {gain * price:+.1f}c for {spent}c"
        elif repeat:
            status = "Same as before"
        title_right = f"{p.colour} · {p.rarity.capitalize()}"
        if (pend.get("cond") or {}).get("white"):
            title_right += " · white map"               # so a forgotten white-map switch is noticed
        return {"kind": "node", "title": p.name or "?", "title_right": title_right, "type": p.colour,
                "lines": lines, "status": status, "footer": self._footer(), "anchor": ev.box}

    # ------------------------------------------------------------------ Numpad 3
    def result(self, ev: ResultEvent) -> Outcome:
        if not ev.stacks:
            return Outcome(None, None, "result: no lifeforce on screen - ignored")
        readable = all(a is not None for a, _ in ev.stacks)
        map_id = self._map(ev)
        self._close_if_left_map(map_id)
        when = ev.time.isoformat(timespec="seconds")
        w = ev.wisps
        total = sum(a for a, _ in ev.stacks if a is not None)

        target, replaced = None, False
        if self.pending:
            target = self.pending
        elif self.last_done and (ev.time - self.last_done["time"]).total_seconds() <= RESULT_REPLACE_SECONDS:
            target, replaced = self.last_done, True

        if target is None:
            cur = self.con.execute(
                "INSERT INTO encounters (map_id, kind, status, source, result_time, lifeforce_total, "
                "wisps_yellow_result, wisps_blue_result, wisps_purple_result) VALUES (?, '', 'no node', ?, ?, ?, ?, ?, ?)",
                (map_id, self.source, when, total if readable else None,
                 w.get("yellow", 0), w.get("blue", 0), w.get("purple", 0)))
            eid = cur.lastrowid
            dblib.set_stacks(self.con, eid, ev.stacks)
            dblib.add_read(self.con, eid, "result", when, ev.screenshot, ev.crop,
                           {"stacks": ev.stacks, "wisps": w}, readable)
            self.con.commit()
            popup = {"kind": "result", "title": self._stack_text(ev.stacks), "lines": [
                ("! No plant read before this - press Numpad 1 at the plant first", "bad")],
                "footer": self._footer(), "anchor": ev.box}
            return Outcome("buzz", popup, f"result with no plant read first: {ev.stacks}", eid)

        eid = target["id"]
        colours = {c for _, c in ev.stacks if c}
        mixed_ok = "all_colours" in target["mods"] or target["kind"] == "specimen"
        colour_ok = mixed_ok or not target["colour"] or colours <= {target["colour"]}
        if not readable:
            status = "unreadable"
        elif not target["clean"] or not colour_ok:
            status = "needs review"
        else:
            status = "ok"
        price = self.estimator.prices.chaos_each(target["colour"]) if (self.estimator and target["colour"]) else None
        self.con.execute(
            "UPDATE encounters SET status = ?, result_time = ?, lifeforce_total = ?, "
            "wisps_yellow_result = ?, wisps_blue_result = ?, wisps_purple_result = ?, price = ? WHERE id = ?",
            (status, when, total if readable else None, w.get("yellow", 0), w.get("blue", 0), w.get("purple", 0),
             price, eid))
        dblib.set_stacks(self.con, eid, ev.stacks)
        dblib.add_read(self.con, eid, "result", when, ev.screenshot, ev.crop,
                       {"stacks": ev.stacks, "wisps": w}, readable)
        self.con.commit()
        self.pending = None
        self.last_done = dict(target, time=ev.time)

        if status == "ok" and self.estimator is not None:
            self.estimator.refit()                 # learn from this plant straight away
        lines = [(f"{target['name']}  ·  {target['colour'] or ''}", "sub")]
        est = self.con.execute("SELECT est, est_lo, est_hi FROM encounters WHERE id = ?", (eid,)).fetchone()
        if readable and est and est[0]:
            diff = total / est[0] - 1
            where = ("inside the likely range" if est[1] <= total <= est[2]
                     else "above the likely range" if total > est[2] else "below the likely range")
            lines.append((f"Estimate was {est[0]:,.0f}  →  {diff * 100:+.0f}%  ({where})",
                          "good" if total >= est[1] else "bad"))
        if readable and status == "ok" and target.get("rerolls"):
            first = target.get("est_first") or self._first_estimate(target)
            spent = target["rerolls"] * CHAOS_PER_REROLL
            if first:
                gain = total - first
                text = f"Rerolls: {spent} chaos  →  {gain:+,.0f} vs the first roll's estimate"
                if price:
                    text += f"  ≈ {gain * price:+.1f}c, net {gain * price - spent:+.1f}c"
                lines.append((text, "good" if (price and gain * price >= spent) or (not price and gain > 0) else "bad"))
            else:
                lines.append((f"Rerolls: {spent} chaos (the first roll couldn't be estimated)", "sub"))
        if not readable:
            lines.append(("! Couldn't read the amount - press Numpad 3 again", "bad"))
        elif not colour_ok:
            lines.append((f"! Lifeforce colour doesn't match the {target['colour']} plant - saved for review", "bad"))
        elif not target["clean"]:
            lines.append(("! The plant wasn't read cleanly - saved for review", "bad"))
        popup = {"kind": "result", "title": self._stack_text(ev.stacks) + (" (replaced)" if replaced else ""),
                 "lines": lines, "footer": self._footer(), "anchor": ev.box,
                 "type": next(iter(colours)) if len(colours) == 1 else None}
        return Outcome("result" if status == "ok" else "buzz", popup,
                       f"result {status:12} {target['name']}: {ev.stacks}" + (" (replaced)" if replaced else ""), eid)

    @staticmethod
    def _stack_text(stacks) -> str:
        parts = [f"{a:,} {c}" if a is not None else f"? {c or ''}".strip() for a, c in stacks]
        return " + ".join(parts) + " lifeforce"

    def finish(self):
        """End of a replay / shutdown: an open plant read never got a result - dropped."""
        if self.pending:
            self._drop_pending()
            self.con.commit()
