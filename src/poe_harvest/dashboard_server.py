"""
The dashboard: a page in your browser with your Harvest data - lifeforce per plant type, which
mods come with more or less lifeforce, estimates, rerolls, the encounter log with the cut-outs of
every read, and the review queue where misreads can be fixed. Served by the app itself, only to
this PC (http://127.0.0.1).

Corrections are written to the database AND to manual_fixes.json, so a rebuild keeps them.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from . import __version__, db as dblib, model as mdl, mods as modlib, settings as settings_mod
from .prices import Prices
from .tracker_core import CHAOS_PER_REROLL

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "web", "index.html")
NAME_RE = re.compile(r"^[\w\-.]+\.png$")
log = logging.getLogger(__name__)
_lock = threading.Lock()
_prices = None


def S():
    return settings_mod.current()


def data_dir():
    return S().data_dir


def fixes_path():
    return os.path.join(data_dir(), "manual_fixes.json")


def icons_path():
    return os.path.join(data_dir(), "mod_icons.json")


# ---------------------------------------------------------------------------
# small JSON files (kept outside the database so a rebuild doesn't lose them)
# ---------------------------------------------------------------------------
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def connect():
    con = dblib.connect(S().db_path)
    con.execute("PRAGMA busy_timeout = 5000")          # the tracker may be writing at the same time
    return con


# ---------------------------------------------------------------------------
# data for the page
# ---------------------------------------------------------------------------
def build_data():
    con = connect()
    icons = load_json(icons_path(), {})
    out_mods = []
    for mid, wording, kind in modlib.MODS:
        if kind == "implicit":
            continue
        affix = "prefix" if mid in modlib.PREFIXES else "suffix" if mid in modlib.SUFFIXES else ""
        out_mods.append({"id": mid, "wording": wording, "kind": kind, "affix": affix,
                         "range": modlib.value_range(mid), "icon": icons.get(mid, "auto")})
    rewards_known = [{"id": "reward:" + r, "wording": f"Tier 3 Sacred Fauna drop additional {r}",
                      "range": modlib.value_range("reward:" + r)} for r in modlib.KNOWN_REWARDS]

    rows = con.execute("""SELECT e.*, m.name AS map_name, m.area_id, m.instance FROM encounters e
                          LEFT JOIN maps m ON m.id = e.map_id
                          ORDER BY COALESCE(e.node_time, e.result_time)""").fetchall()
    enc = []
    for e in rows:
        eid = e["id"]
        ms = {r[0]: r[1] for r in con.execute("SELECT mod_id, value FROM encounter_mods WHERE encounter_id = ?", (eid,))}
        rw = {r[0]: r[1] for r in con.execute("SELECT item, count FROM encounter_rewards WHERE encounter_id = ?", (eid,))}
        st = [[r[0], r[1]] for r in con.execute("SELECT amount, colour FROM lifeforce_stacks WHERE encounter_id = ?", (eid,))]
        reads = con.execute("SELECT key, time, screenshot, crop, parsed, ok FROM reads WHERE encounter_id = ? ORDER BY id",
                            (eid,)).fetchall()
        node = [r for r in reads if r["key"] == "node"]
        res = [r for r in reads if r["key"] == "result"]
        last_node = node[-1] if node else None
        last_res = res[-1] if res else None
        problems = []
        if last_node:
            p = json.loads(last_node["parsed"] or "{}")
            problems = list(p.get("problems", [])) + [f"not recognised: {u}" for u in p.get("unknown", [])]

        def wisp(col):
            v = e[f"wisps_{col}_node"]
            return v if v is not None and (v or not e[f"wisps_{col}_result"]) else e[f"wisps_{col}_result"]

        enc.append({
            "id": eid, "map": (e["map_name"] or "?").rstrip("_"), "area": e["area_id"], "instance": e["instance"],
            "status": e["status"], "kind": e["kind"] or "", "plant": e["plant_name"] or "",
            "type": e["plant_type"] or "", "rarity": e["rarity"] or "",
            "node_time": e["node_time"], "result_time": e["result_time"],
            "lifeforce": e["lifeforce_total"], "stacks": st,
            "rerolls": e["rerolls"] or 0, "chaos": e["chaos_spent"] or 0,
            "wisps": {c: wisp(c) for c in ("yellow", "blue", "purple")},
            "mods": ms, "rewards": rw, "note": e["note"] or "", "source": e["source"],
            "node_shot": os.path.basename(last_node["screenshot"]) if last_node and last_node["screenshot"] else "",
            "node_cut": os.path.basename(last_node["crop"]) if last_node and last_node["crop"] else "",
            "result_shot": os.path.basename(last_res["screenshot"]) if last_res and last_res["screenshot"] else "",
            "result_cut": os.path.basename(last_res["crop"]) if last_res and last_res["crop"] else "",
            "node_reads": len(node), "problems": problems,
            "shown_est": e["est"], "shown_lo": e["est_lo"], "shown_hi": e["est_hi"], "price": e["price"],
            "atlas": e["atlas_drops"],
        })
    model_info = model_data(con, icons, enc)
    con.close()
    return {"generated": datetime.now().isoformat(timespec="seconds"), "flora_effect": modlib.flora_effect(),
            "version": __version__, "atlas_drops": S().get("atlas_drops"),
            "mods": out_mods, "rewards": rewards_known, "encounters": enc, "model": model_info, "icons": icons}


def model_data(con, icons, enc) -> dict:
    """Phase 4: the fitted model, its accuracy, every mod's effect, rerolls in chaos, prices."""
    global _prices
    if _prices is None:
        _prices = Prices(data_dir(), league=lambda: S().get("league"))
    _prices.refresh_soon()
    out = {"prices": _prices.info(), "show_from": mdl.SHOW_FROM, "rough_until": mdl.ROUGH_UNTIL}
    atlas = S().get("atlas_drops")
    m = mdl.choose(con, S().get("starter_estimates") or "auto", default_atlas=atlas)
    if m is None:
        out["fitted"] = False
        return out
    data = mdl.plants(con)
    seen = {}
    for p in data:
        for mid in p["mods"]:
            key = mdl.REWARD if mid in mdl.REWARD_IDS else mid
            seen[key] = seen.get(key, 0) + 1
        seen[mdl.REWARD] = seen.get(mdl.REWARD, 0) + len(p["rewards"])
    effects = []
    for mid in mdl.MOD_IDS + [mdl.REWARD]:
        tv = mdl.typical_value(mid) if mid in mdl.MOD_IDS else None
        e, lo, hi = m.mod_effect(mid, tv)
        step = m.per_step(mid)
        effects.append({"id": mid, "n": seen.get(mid, 0), "effect": e, "lo": lo, "hi": hi,
                        "typical_value": tv, "icon": m.icon(e, lo, hi, icons.get(mid, "auto")),
                        "auto_icon": m.icon(e, lo, hi), "step": step[0] if step else None,
                        "per_step": step[1] if step else None})
    wisps = []
    for y in (1000, 2000, 3000, 4000, 5000):
        e, lo, hi = m.wisp_effect(y)
        wisps.append({"yellow": y, "effect": e, "lo": lo, "hi": hi, "icon": m.icon(e, lo, hi)})
    # reroll ledger: every counted plant that was rerolled. Each roll you read gets today's estimate;
    # "extra" = actual lifeforce minus what the first roll would likely have given (its estimate),
    # "expected extra" = final roll's estimate minus the first roll's (luck taken out).
    yellow_by_map = {mid: v.get("yellow") for mid, v in mdl.map_wisps(con).items()}
    rerolls = []
    for e in enc:
        if not e["rerolls"] or e["status"] != "ok" or e["kind"] != "flora" or e["type"] not in mdl.TYPES:
            continue
        map_id = con.execute("SELECT map_id FROM encounters WHERE id = ?", (e["id"],)).fetchone()[0]
        rolls, last = [], None
        for parsed, ok in con.execute("SELECT parsed, ok FROM reads WHERE encounter_id = ? AND key = 'node' ORDER BY id",
                                      (e["id"],)):
            pj = json.loads(parsed or "{}")
            if not ok:
                continue                       # a read that failed isn't a roll
            key = json.dumps([sorted((pj.get("mods") or {}).keys()), sorted((pj.get("rewards") or {}).keys()),
                              pj.get("name")])
            if key == last:
                continue
            last = key
            est = None
            if ok and pj.get("mods") and pj.get("type") == e["type"]:
                est = m.estimate({"type": e["type"], "mods": pj["mods"], "rewards": pj.get("rewards", {}),
                                  "yellow": yellow_by_map.get(map_id) or 0,
                                  "atlas": e["atlas"] if e["atlas"] is not None else atlas})[0]
            rolls.append({"name": pj.get("name", ""), "est": est})
        price_now = _prices.chaos_each(e["type"])
        price = e["price"] or price_now
        first = rolls[0]["est"] if rolls else None
        final = rolls[-1]["est"] if len(rolls) > 1 else None
        row = {"id": e["id"], "rolls": rolls, "rerolls": e["rerolls"], "chaos": e["chaos"], "type": e["type"],
               "actual": e["lifeforce"], "first": first, "final": final, "price": price,
               "price_at_result": bool(e["price"]), "reads_missing": len(rolls) < e["rerolls"] + 1}
        if first is not None:
            row["extra"] = e["lifeforce"] - first
            row["expected_extra"] = final - first if final is not None else None
            if price:
                row["worth"] = row["extra"] * price
                row["net"] = row["worth"] - e["chaos"]
                if row["expected_extra"] is not None:
                    row["expected_worth"] = row["expected_extra"] * price
                    row["expected_net"] = row["expected_worth"] - e["chaos"]
        rerolls.append(row)
    # estimates per encounter: leave-one-out for counted plants (an honest test), else from all plants
    for e in enc:
        if e["id"] in m.loo_pred:
            e["est"] = m.loo_pred[e["id"]]
        elif e["kind"] == "flora" and e["type"] in mdl.TYPES and e["mods"]:
            e["est"] = m.estimate({"type": e["type"], "mods": {k: v for k, v in e["mods"].items()},
                                   "rewards": e["rewards"], "yellow": e["wisps"].get("yellow") or 0,
                                   "atlas": e["atlas"] if e["atlas"] is not None else atlas})[0]
        else:
            e["est"] = None
    out.update(fitted=True, usable=m.usable, rough=m.rough, effects=effects, wisps=wisps, rerolls=rerolls,
               atlas_effect=(m.atlas_effect(atlas) if atlas else None), **m.summary(atlas or 0))
    return out


# ---------------------------------------------------------------------------
# corrections from the page
# ---------------------------------------------------------------------------
def fix_key(row_shot, row_cut):
    return row_shot or row_cut


def apply_fix(body: dict) -> dict:
    """body: {"id": encounter id, "action": "save" | "accept" | "exclude" | "include",
              "mods": {...}, "rewards": {...}, "type": "Wild", "stacks": [[amount, colour], ...]}"""
    eid = int(body["id"])
    action = body.get("action", "save")
    with _lock:
        con = connect()
        fixes = load_json(fixes_path(), {})
        reads = con.execute("SELECT key, screenshot, crop FROM reads WHERE encounter_id = ? ORDER BY id", (eid,)).fetchall()
        node = [r for r in reads if r["key"] == "node"]
        res = [r for r in reads if r["key"] == "result"]
        node_key = fix_key(os.path.basename(node[-1]["screenshot"] or ""), os.path.basename(node[-1]["crop"] or "")) if node else ""
        res_key = fix_key(os.path.basename(res[-1]["screenshot"] or ""), os.path.basename(res[-1]["crop"] or "")) if res else ""
        any_key = node_key or res_key
        e = con.execute("SELECT * FROM encounters WHERE id = ?", (eid,)).fetchone()
        if e is None:
            con.close()
            return {"ok": False, "error": "encounter not found"}
        note = f"fixed in dashboard {datetime.now():%Y-%m-%d %H:%M}"

        if action == "save":
            # only what actually changed is stored as a correction, so the reader's own
            # result is still used for everything else on a rebuild
            cur_mods = {r[0]: r[1] for r in con.execute("SELECT mod_id, value FROM encounter_mods WHERE encounter_id = ?", (eid,))}
            cur_rewards = {r[0]: r[1] for r in con.execute("SELECT item, count FROM encounter_rewards WHERE encounter_id = ?", (eid,))}
            cur_stacks = [[r[0], r[1]] for r in con.execute("SELECT amount, colour FROM lifeforce_stacks WHERE encounter_id = ?", (eid,))]
            if "mods" in body and node_key:
                mods_in = {k: (None if v in ("", None) else int(v)) for k, v in body["mods"].items()}
                rewards_in = {k: int(v) for k, v in body.get("rewards", {}).items() if v not in ("", None)}
                if mods_in != cur_mods or rewards_in != cur_rewards:
                    dblib.set_mods(con, eid, mods_in, rewards_in)
                    fixes.setdefault(node_key, {}).update({"mods": mods_in, "rewards": rewards_in, "note": note})
            if body.get("type") and node_key and body["type"] != e["plant_type"]:
                con.execute("UPDATE encounters SET plant_type = ? WHERE id = ?", (body["type"], eid))
                fixes.setdefault(node_key, {}).update({"type": body["type"], "note": note})
            if "stacks" in body and res_key:
                stacks = [[int(a), c] for a, c in body["stacks"] if a not in ("", None)]
                if stacks != cur_stacks:
                    dblib.set_stacks(con, eid, [(a, c) for a, c in stacks])
                    con.execute("UPDATE encounters SET lifeforce_total = ? WHERE id = ?",
                                (sum(a for a, _ in stacks) if stacks else None, eid))
                    fixes.setdefault(res_key, {}).update({"lifeforce": stacks, "note": note})
            if body.get("rerolls") not in (None, "") and node_key and int(body["rerolls"]) != (e["rerolls"] or 0):
                n = max(0, int(body["rerolls"]))
                con.execute("UPDATE encounters SET rerolls = ?, chaos_spent = ? WHERE id = ?", (n, n * CHAOS_PER_REROLL, eid))
                fixes.setdefault(node_key, {}).update({"rerolls": n, "note": note})
            if body.get("atlas") not in (None, "") and node_key and float(body["atlas"]) != e["atlas_drops"]:
                con.execute("UPDATE encounters SET atlas_drops = ? WHERE id = ?", (float(body["atlas"]), eid))
                fixes.setdefault(node_key, {}).update({"atlas": float(body["atlas"]), "note": note})
            action = "accept"                       # saving a correction also confirms it

        error = None
        if action == "accept":
            e = con.execute("SELECT * FROM encounters WHERE id = ?", (eid,)).fetchone()
            if e["lifeforce_total"] is None or not e["kind"]:
                error = ("Saved, but not counted: it needs both a plant read and a lifeforce amount. "
                         "Exclude it if the plant wasn't read.")
            else:
                con.execute("UPDATE encounters SET status = 'ok', note = ? WHERE id = ?", (note, eid))
                for k in (node_key, res_key):
                    if k in fixes:
                        fixes[k].pop("exclude", None)
                if node_key:
                    fixes.setdefault(node_key, {}).update({"accept": True, "note": note})
        elif action == "exclude":
            con.execute("UPDATE encounters SET status = 'excluded', note = ? WHERE id = ?",
                        (f"excluded in dashboard {datetime.now():%Y-%m-%d %H:%M}", eid))
            if any_key:
                fixes.setdefault(any_key, {}).update({"exclude": True, "note": "excluded in dashboard"})
        elif action == "include":
            status = ("ok" if e["lifeforce_total"] is not None else "dropped") if e["kind"] else "no node"
            con.execute("UPDATE encounters SET status = ? WHERE id = ?", (status, eid))
            for k in (node_key, res_key):
                if k in fixes:
                    fixes[k].pop("exclude", None)
                    if set(fixes[k]) <= {"note"}:
                        del fixes[k]
        con.commit()
        con.close()
        fixes["_note"] = ("Corrections you've told me about or made in the dashboard. Keyed by screenshot file; "
                          "applied when screenshots are read or imported.")
        save_json(fixes_path(), fixes)
        export_csv()
    return {"ok": False, "error": error} if error else {"ok": True}


def set_icon(body: dict) -> dict:
    with _lock:
        icons = load_json(icons_path(), {})
        if body.get("icon", "auto") == "auto":
            icons.pop(body["id"], None)
        else:
            icons[body["id"]] = body["icon"]
        save_json(icons_path(), icons)
        con = connect()
        con.execute("UPDATE mods SET icon_override = ? WHERE id = ?",
                    (None if body.get("icon") == "auto" else body.get("icon"), body["id"]))
        con.commit()
        con.close()
    return {"ok": True}


def export_csv():
    try:
        from . import importer
        con = connect()
        importer.export_csv(con, os.path.join(data_dir(), "encounters.csv"))
        con.close()
    except Exception:
        pass                                          # e.g. the CSV is open in Excel


# ---------------------------------------------------------------------------
# web server (this PC only)
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass                                          # keep the window quiet

    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _host_ok(self):
        """Only pages served from this PC (stops other websites reaching it by DNS tricks)."""
        port = self.server.server_port
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def do_GET(self):
        path = urlparse(self.path).path
        if not self._host_ok():
            return self._send(403, b"", "text/plain")
        try:
            if path in ("/", "/index.html"):
                if not os.path.isfile(PAGE):
                    return self._send(500, b"The dashboard page is missing - reinstall PoE Harvest Tracker", "text/plain")
                with open(PAGE, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif path == "/api/data":
                self._json(build_data())
            elif path.startswith("/img/"):
                _, _, kind, name = path.split("/", 3)
                name = unquote(name)
                if not NAME_RE.match(name):
                    return self._send(404, b"", "text/plain")
                folder = (os.path.join(data_dir(), "reads", name[:10]) if kind == "cut"
                          else S().screenshots_dir if kind == "shot" else None)
                file = os.path.join(folder, name) if folder else None
                if not file or not os.path.isfile(file):
                    return self._send(404, b"", "text/plain")
                with open(file, "rb") as f:
                    self._send(200, f.read(), "image/png")
            else:
                self._send(404, b"not found", "text/plain")
        except (ConnectionError, BrokenPipeError):
            pass

    def do_POST(self):
        path = urlparse(self.path).path
        if not self._host_ok() or self.headers.get("Origin") not in (None, f"http://127.0.0.1:{self.server.server_port}",
                                              f"http://localhost:{self.server.server_port}"):
            return self._json({"ok": False, "error": "not allowed"}, 403)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if path == "/api/fix":
                self._json(apply_fix(body))
            elif path == "/api/icon":
                self._json(set_icon(body))
            else:
                self._json({"ok": False, "error": "unknown"}, 404)
        except Exception as exc:
            self._json({"ok": False, "error": str(exc)}, 500)


class Dashboard:
    """The web server, on its own thread. Tries the port from Settings and the next 20."""

    def __init__(self, port: int = 8765):
        self.port = port
        self.server = None
        self.thread = None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/"

    def start(self):
        first = self.port
        while True:
            try:
                self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
                break
            except OSError:
                self.port += 1
                if self.port > first + 20:
                    raise
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name="dashboard", daemon=True)
        self.thread.start()
        log.info("dashboard on %s", self.url)
        return self

    def open(self):
        webbrowser.open(self.url)

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
