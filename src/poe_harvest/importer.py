"""
Read saved screenshots (listed in capture_log.csv) into the database, using exactly the same rules
as the live tracker. Screenshots already in the database are skipped, so it's safe to run again.
Used by "Rebuild database" in Settings; also writes encounters.csv for Excel.
"""
from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime

import cv2

from . import db as dblib, mods, reader, tracker_core

log = logging.getLogger(__name__)


def load_fixes(path):
    if not os.path.exists(path):
        return {}
    return {k: v for k, v in json.load(open(path, encoding="utf-8")).items() if not k.startswith("_")}


def save_cut(reads_dir, name, img, box):
    """Keep the cut-out (tooltip or labels) next to the tracker's own, for the dashboard."""
    path = os.path.join(reads_dir, name[:10], name)
    if not os.path.exists(path):
        x0, y0, x1, y1 = [int(v) for v in box]
        crop = img[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
        if crop.size:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            cv2.imwrite(path, crop)
    return path if os.path.exists(path) else ""


def apply_node_fix(parsed, fix):
    """Corrections made in the dashboard for this plant read (see dashboard.py)."""
    if not fix:
        return parsed
    if "mods" in fix:
        kinds = {mid: kind for mid, _, kind in mods.MODS}
        parsed.mods = [mods.ParsedMod(mid, val, "fixed by hand", 1.0, kinds.get(mid, "mod"))
                       for mid, val in fix["mods"].items()]
        parsed.rewards = dict(fix.get("rewards", {}))
        for item, count in parsed.rewards.items():
            parsed.mods.append(mods.ParsedMod("reward:" + item, count, "fixed by hand", 1.0, "reward"))
        parsed.unknown, parsed.problems = [], []
    if fix.get("type"):
        parsed.colour = fix["type"]
    if fix.get("accept"):
        parsed.unknown, parsed.problems = [], []
    return parsed


def apply_exclusions(con, fixes):
    """Encounters marked in the dashboard: "accept" counts it even if a check failed (e.g. the
    lifeforce colour didn't match), "exclude" leaves it out of every statistic."""
    def ids(name):
        return [r[0] for r in con.execute(
            "SELECT DISTINCT encounter_id FROM reads WHERE encounter_id IS NOT NULL AND (screenshot = ? OR crop LIKE ?)",
            (name, "%" + name))]
    for name, fix in fixes.items():
        if fix.get("accept"):
            for eid in ids(name):
                con.execute("""UPDATE encounters SET status = 'ok' WHERE id = ? AND status IN ('needs review', 'unreadable')
                               AND lifeforce_total IS NOT NULL AND kind != ''""", (eid,))
        if fix.get("note"):
            for eid in ids(name):
                con.execute("UPDATE encounters SET note = ? WHERE id = ?", (fix["note"], eid))
        for key, col in (("atlas", "atlas_drops"), ("tree", "tree_lifeforce"), ("white", "white_map")):
            if fix.get(key) is not None:           # map setup corrected in the dashboard
                for eid in ids(name):
                    con.execute(f"UPDATE encounters SET {col} = ? WHERE id = ?", (fix[key], eid))
        if fix.get("rerolls") is not None:         # reroll count corrected in the dashboard
            for eid in ids(name):
                con.execute("UPDATE encounters SET rerolls = ?, chaos_spent = ? WHERE id = ?",
                            (fix["rerolls"], fix["rerolls"] * 1, eid))
    for name, fix in fixes.items():
        if fix.get("exclude"):
            for eid in ids(name):
                con.execute("UPDATE encounters SET status = 'excluded' WHERE id = ?", (eid,))
    con.commit()


def import_folder(shots, con, engine, fixes, quiet=False, reads_dir=None, label_colour=None, progress=None):
    log_path = os.path.join(shots, "capture_log.csv")
    if not os.path.exists(log_path):
        log.info("no capture_log.csv in %s", shots)
        return []
    with open(log_path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if os.path.exists(os.path.join(shots, r["file"]))]
    done = {os.path.basename(r[0]) for r in con.execute("SELECT screenshot FROM reads WHERE screenshot != ''")}
    unique = {}
    for r in rows:                     # a press logged twice (two tools running) counts once
        unique.setdefault(r["file"], r)
    rows = sorted(unique.values(), key=lambda r: r["time"])
    kept = []
    for r in rows:                     # ...even when the two copies are a millisecond apart
        if kept and kept[-1]["key"] == r["key"] and abs(
                (datetime.fromisoformat(r["time"]) - datetime.fromisoformat(kept[-1]["time"])).total_seconds()) < 0.25:
            continue
        kept.append(r)
    rows = [r for r in kept if r["file"] not in done]
    tracker = tracker_core.EncounterTracker(con, source="screenshots")
    reads_dir = reads_dir or os.path.join(os.path.dirname(os.path.abspath(shots)), "data", "reads")
    outcomes, instance, last_area = [], 0, None
    for i_row, r in enumerate(rows):
        if progress:
            progress(i_row, len(rows))
        f = r["file"]
        area = r.get("area") or None
        if area != last_area:
            instance += 1
            last_area = area
        when = datetime.fromisoformat(r["time"])
        inst = f"shots-{when:%Y%m%d}-{instance}"
        cursor = (int(r["cursor_x"]), int(r["cursor_y"])) if r.get("cursor_x") else None
        img = cv2.imread(os.path.join(shots, f))
        path = f                                   # stored by name, so re-runs can skip it
        from_cut = False
        if img is None:                            # damaged file: use the tracker's cut-out if there is one
            cut = os.path.join(reads_dir, f[:10], f)
            img = cv2.imread(cut) if os.path.exists(cut) else None
            cursor, from_cut = None, True
            if not quiet or img is None:
                log.info("%s: screenshot damaged - %s", f, "using the saved cut-out" if img is not None else "skipped")
            if img is None:
                continue
        if f.endswith("_node.png"):
            res = reader.read_screenshot(img, engine, cursor, ("tooltip", "wisps"))
            img = res["image"]                     # boxes are in this (1440-tall) frame
            if res["tooltip"] is None:
                dblib.add_read(con, None, "node", r["time"], f, "", {"note": "no tooltip on screen"}, False)
                con.commit()
                outcomes.append((f, None))
                continue
            tt = res["tooltip"]
            box = (tt.box[0], tt.box[1], tt.box[2], tt.bottom)
            parsed = apply_node_fix(mods.parse_tooltip(tt), fixes.get(f))
            cut = save_cut(reads_dir, f, img, (box[0], box[1], box[2], box[3] + 12)) if not from_cut else cut
            num = lambda k: float(r[k]) if r.get(k) not in (None, "") else None   # noqa: E731
            cond = {"atlas": num("atlas"), "tree": num("tree"),
                    "white": None if r.get("white") in (None, "") else r["white"] == "1",
                    "level": int(num("level")) if num("level") is not None else None}
            ev = tracker_core.NodeEvent(when, area, inst, parsed, res["wisps"], box, path, cut, cond)
            out = tracker.node(ev)
        else:
            res = reader.read_screenshot(img, engine, cursor, ("lifeforce", "wisps"), label_colour=label_colour)
            img = res["image"]
            stacks = [(l.amount, l.colour) for l in res["lifeforce"]]
            boxes = [(l.x0, l.y0, l.x1, l.y1) for l in res["lifeforce"]]
            if fixes.get(f, {}).get("lifeforce") is not None:
                stacks = [tuple(s) for s in fixes[f]["lifeforce"]]
            box = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                   max(b[2] for b in boxes), max(b[3] for b in boxes)) if boxes else None
            cut = cut if from_cut else save_cut(reads_dir, f, img, box) if box else ""
            ev = tracker_core.ResultEvent(when, area, inst, stacks, res["wisps"], box, path, cut)
            out = tracker.result(ev)
            if not stacks:
                dblib.add_read(con, None, "result", r["time"], f, "", {"note": "no lifeforce on screen"}, False)
                con.commit()
            if fixes.get(f, {}).get("lifeforce") is not None and out.encounter_id:
                con.execute("UPDATE encounters SET note = ? WHERE id = ?", (fixes[f].get("note", "fixed by hand"),
                                                                           out.encounter_id))
                con.commit()
        outcomes.append((f, out))
    tracker.finish()
    apply_exclusions(con, fixes)
    return outcomes


def export_csv(con, path):
    mod_ids = [m[0] for m in mods.MODS if m[2] == "mod"]
    cols = ["id", "map", "status", "kind", "plant", "type", "rarity", "lifeforce_total", "lifeforce_stacks",
            "rerolls", "chaos_spent", "atlas_drops", "tree_lifeforce", "white_map", "area_level",
            "wisps_yellow", "wisps_blue", "wisps_purple"] + mod_ids + \
           ["rewards", "node_time", "result_time", "source", "note"]
    q = """SELECT e.*, m.name AS map_name FROM encounters e LEFT JOIN maps m ON m.id = e.map_id ORDER BY
           COALESCE(e.node_time, e.result_time)"""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=cols)
        wr.writeheader()
        for e in con.execute(q).fetchall():
            ms = dict(con.execute("SELECT mod_id, value FROM encounter_mods WHERE encounter_id = ?", (e["id"],)).fetchall())
            rw = con.execute("SELECT item, count FROM encounter_rewards WHERE encounter_id = ?", (e["id"],)).fetchall()
            st = con.execute("SELECT amount, colour FROM lifeforce_stacks WHERE encounter_id = ?", (e["id"],)).fetchall()
            row = {"id": e["id"], "map": e["map_name"], "status": e["status"], "kind": e["kind"],
                   "plant": e["plant_name"], "type": e["plant_type"], "rarity": e["rarity"],
                   "lifeforce_total": e["lifeforce_total"],
                   "lifeforce_stacks": " + ".join(f"{a if a is not None else '?'} {c or ''}".strip() for a, c in st),
                   "rerolls": e["rerolls"], "chaos_spent": e["chaos_spent"], "atlas_drops": e["atlas_drops"],
                   "tree_lifeforce": e["tree_lifeforce"], "white_map": e["white_map"], "area_level": e["area_level"],
                   "wisps_yellow": e["wisps_yellow_node"] or e["wisps_yellow_result"],
                   "wisps_blue": e["wisps_blue_node"] or e["wisps_blue_result"],
                   "wisps_purple": e["wisps_purple_node"] or e["wisps_purple_result"],
                   "rewards": "; ".join(f"{i} x{c}" for i, c in rw),
                   "node_time": e["node_time"], "result_time": e["result_time"], "source": e["source"],
                   "note": e["note"]}
            for mid in mod_ids:
                if mid in ms:
                    row[mid] = "yes" if ms[mid] is None else ms[mid]
            wr.writerow(row)
