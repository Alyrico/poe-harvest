"""
PoE Harvest Tracker - the app.

    tray icon (ui thread)      hotkeys, popup, tray menu
    worker thread              one screenshot per hotkey press -> read -> database -> popup
    dashboard thread           the local web page (http://127.0.0.1:8765)
    settings window            tkinter, on its own thread when open
    main thread                waits until Quit

    PoE Harvest Tracker.exe [--background] [--debug]
        --background   started with Windows: don't open anything, just sit in the tray
        --debug        also log to the console
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import queue
import subprocess
import sys
import threading
import time
import webbrowser

from . import __version__, win

APP_NAME = "PoE Harvest Tracker"
REPO = "Alyrico/poe-harvest"
RELEASES_URL = f"https://github.com/{REPO}/releases"
GUIDE_URL = f"https://github.com/{REPO}#set-up-your-loot-filter"
log = logging.getLogger("poe_harvest")

# tray menu commands
CMD_DASHBOARD, CMD_SETTINGS, CMD_PAUSE, CMD_ATLAS, CMD_FOLDER, CMD_UPDATE, CMD_GUIDE, CMD_QUIT, CMD_WHITE = range(1, 10)


def setup_logging(folder, debug=False):
    os.makedirs(folder, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(os.path.join(folder, "app.log"), maxBytes=1_000_000,
                                                   backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if debug and sys.stderr:
        root.addHandler(logging.StreamHandler())

    def excepthook(exc_type, exc, tb):
        log.error("unexpected error", exc_info=(exc_type, exc, tb))
    sys.excepthook = excepthook
    threading.excepthook = lambda a: log.error("unexpected error in %s", a.thread.name if a.thread else "?",
                                               exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def asset(name):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", name)


def newer(a: str, b: str) -> bool:
    """Is version a newer than b? ("1.2.0" > "1.1.9")"""
    def parts(v):
        return tuple(int(x) for x in v.lstrip("v").split("-")[0].split(".") if x.isdigit())
    try:
        return parts(a) > parts(b)
    except ValueError:
        return False


class App:
    def __init__(self, settings, engine, background=False):
        from . import capture, dashboard_server, db as dblib, estimator, tracker_core, zones
        self.settings = settings
        self.engine = engine
        self.background = background
        self.jobs = queue.Queue()
        self.quit_event = threading.Event()
        self.restart = False
        self.paused = False
        self.busy = threading.Lock()                 # held while a key press is handled / during a rebuild
        self.update = None                           # (version, url) when a newer release exists
        self.settings_window = None
        self.status = "Starting"

        win.sound_settings.update(on=bool(settings["sounds"]), volume=int(settings["volume"]))
        self.con = dblib.connect(settings.db_path)
        self.estimator = estimator.Estimator(self.con, settings.data_dir, settings=settings)
        self.tracker = tracker_core.EncounterTracker(self.con, source="live", estimator=self.estimator)
        self.zones = zones.ZoneTracker(settings["poe_log"])
        self.ui = win.Ui(self.on_key, settings["hotkeys"],
                         tray={"icons": {s: asset(f"tray_{s}.ico") for s in ("ready", "paused", "problem")},
                               "menu": self.tray_menu, "command": self.tray_command, "default": CMD_DASHBOARD})
        self.capturer = capture.Capturer(settings, engine, self.tracker, self.zones, self.ui)
        self.dashboard = dashboard_server.Dashboard(int(settings["port"]))
        settings.on_change(self.settings_changed)

    # ------------------------------------------------------------------ running
    def run(self):
        self.ui.start()
        self.dashboard.start()
        worker = threading.Thread(target=self.work, name="worker", daemon=True)
        worker.start()
        threading.Thread(target=self.background_jobs, name="housekeeping", daemon=True).start()
        self.refresh_tray()
        log.info("%s %s started; data in %s; game log %s", APP_NAME, __version__, self.settings.data_dir,
                 self.zones.path or "not found yet")
        if not self.settings["first_run_done"] or self.settings.get("atlas_drops") is None:
            self.open_settings(first_run=True)
        elif self.plants_without_atlas():
            self.open_settings(focus="atlas")             # e.g. after pointing the app at older data
        elif not self.background:
            self.ui.notify(APP_NAME, f"Running in the tray. {win.hotkey_name(self.settings['hotkeys']['node'])} "
                                     f"at a plant, {win.hotkey_name(self.settings['hotkeys']['result'])} at the lifeforce.")
        while not self.quit_event.wait(1.0):
            pass
        self.jobs.put(None)
        worker.join(5)
        with self.busy:
            self.tracker.finish()
            self.export_csv()
        self.dashboard.stop()
        self.ui.stop()
        self.ui.join(3)
        log.info("stopped")
        return 0

    def on_key(self, key, when, cursor, fg):          # ui thread: must be quick
        if not self.paused:
            self.jobs.put((key, when, cursor, fg))

    def work(self):
        import mss
        capture_cls = mss.MSS if hasattr(mss, "MSS") else mss.mss
        with capture_cls() as sct:
            while True:
                job = self.jobs.get()
                if job is None:
                    break
                try:
                    with self.busy:
                        self.capturer.handle(sct, *job)
                    self.refresh_tray()
                except Exception:
                    log.exception("handling a key press failed")
                    win.play("buzz")

    def background_jobs(self):
        """Old screenshots, prices and the update check - after startup, then now and again."""
        from . import capture
        time.sleep(20)
        last_update_check = 0.0
        while not self.quit_event.is_set():
            try:
                if self.settings["save_screenshots"]:
                    n = capture.clean_old_screenshots(self.settings.screenshots_dir,
                                                      int(self.settings["keep_screenshots_days"] or 0))
                    if n:
                        log.info("deleted %d screenshots older than %s days", n, self.settings["keep_screenshots_days"])
                self.estimator.prices.refresh_soon()
                if self.settings["check_updates"] and time.time() - last_update_check > 86400:
                    last_update_check = time.time()
                    self.check_update()
                self.refresh_tray()
            except Exception:
                log.exception("housekeeping failed")
            self.quit_event.wait(3600)

    def check_update(self):
        import json
        import urllib.request
        try:
            req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                         headers={"User-Agent": f"PoE-Harvest-Tracker/{__version__}",
                                                  "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                rel = json.loads(r.read().decode("utf-8"))
            tag = rel.get("tag_name", "")
            if tag and newer(tag, __version__):
                self.update = (tag.lstrip("v"), rel.get("html_url") or RELEASES_URL)
                self.ui.notify("Update available", f"{APP_NAME} {self.update[0]} is out - right-click the tray icon to get it.")
                log.info("update available: %s", tag)
        except Exception as exc:
            log.info("update check failed: %s", exc)

    # ------------------------------------------------------------------ tray
    def plants(self):
        try:
            return self.con.execute("SELECT COUNT(*) FROM encounters WHERE status = 'ok' AND kind = 'flora'").fetchone()[0]
        except Exception:
            return 0

    def refresh_tray(self):
        atlas = self.settings.get("atlas_drops")
        if self.paused:
            state, tip = "paused", f"{APP_NAME} - paused"
        elif not self.zones.path and win.find_poe_window():
            state, tip = "problem", f"{APP_NAME} - can't find the game's log (Settings > Path of Exile folder)"
        else:
            state, tip = "ready", f"{APP_NAME} - ready"
        tip += f"\n{self.plants()} plants · Atlas Drops {'?' if atlas is None else f'{atlas:g}'}%"
        if self.settings.get("white_map"):
            tip += " · white map"
        self.ui.set_tray(state, tip)

    def tray_menu(self):
        atlas = self.settings.get("atlas_drops")
        items = [(CMD_DASHBOARD, "Open dashboard", True, False),
                 (CMD_SETTINGS, "Settings...", True, False),
                 (CMD_PAUSE, "Pause", True, self.paused),
                 None,
                 (CMD_ATLAS, f"Atlas Drops: {'not set' if atlas is None else f'{atlas:g}%'} - change...", True, False),
                 (CMD_WHITE, "White map (no map mods)", True, bool(self.settings.get("white_map"))),
                 (CMD_FOLDER, "Open data folder", True, False),
                 (CMD_GUIDE, "Loot filter guide", True, False)]
        if self.update:
            items += [None, (CMD_UPDATE, f"Update available: {self.update[0]} - download", True, False)]
        items += [None, (CMD_QUIT, "Quit", True, False)]
        return items

    def tray_command(self, cmd):
        if cmd == CMD_DASHBOARD:
            self.dashboard.open()
        elif cmd in (CMD_SETTINGS, CMD_ATLAS):
            self.open_settings(focus="atlas" if cmd == CMD_ATLAS else None)
        elif cmd == CMD_WHITE:
            self.settings.update(white_map=not self.settings.get("white_map"))
        elif cmd == CMD_PAUSE:
            self.paused = not self.paused
            self.refresh_tray()
        elif cmd == CMD_FOLDER:
            os.startfile(self.settings.data_dir)
        elif cmd == CMD_GUIDE:
            webbrowser.open(GUIDE_URL)
        elif cmd == CMD_UPDATE and self.update:
            webbrowser.open(self.update[1])
        elif cmd == CMD_QUIT:
            self.quit()

    def quit(self, restart=False):
        self.restart = restart
        self.quit_event.set()

    # ------------------------------------------------------------------ settings
    def open_settings(self, first_run=False, focus=None):
        from . import settings_window
        if self.settings_window and self.settings_window.alive:
            self.settings_window.raise_window()
            return
        self.settings_window = settings_window.SettingsWindow(self, first_run=first_run, focus=focus)
        self.settings_window.start()

    def settings_changed(self, changes):
        if "hotkeys" in changes:
            self.ui.set_hotkeys(self.settings["hotkeys"])
        if "sounds" in changes or "volume" in changes:
            win.sound_settings.update(on=bool(self.settings["sounds"]), volume=int(self.settings["volume"]))
        if "poe_log" in changes:
            self.zones.set_path(self.settings["poe_log"])
        if "start_with_windows" in changes:
            try:
                win.set_start_with_windows(bool(self.settings["start_with_windows"]))
            except OSError:
                log.exception("couldn't change 'start with Windows'")
        if {"rich_soil", "starter_estimates", "atlas_drops", "tree_lifeforce", "white_map", "t3_base", "league"} & set(changes):
            with self.busy:
                self.estimator.refit()
        self.refresh_tray()

    def export_csv(self):
        from . import importer
        try:
            importer.export_csv(self.con, os.path.join(self.settings.data_dir, "encounters.csv"))
        except Exception:
            pass                                      # e.g. the CSV is open in Excel

    def rebuild(self, progress=None) -> str:
        """Re-read every saved screenshot into a fresh database (Settings > Advanced)."""
        from . import db as dblib, rebuild, tracker_core
        with self.busy:
            self.tracker.finish()
            self.con.close()
            try:
                summary = rebuild.rebuild(self.settings, self.engine, progress)
            finally:
                self.con = dblib.connect(self.settings.db_path)
                self.estimator.con = self.con
                self.estimator.refit()
                self.tracker = tracker_core.EncounterTracker(self.con, source="live", estimator=self.estimator)
                self.capturer.tracker = self.tracker
        log.info(summary)
        self.refresh_tray()
        return summary

    def stamp_missing_atlas(self, value):
        """Plants saved before Atlas Drops was set get the value you give (Settings, first start)."""
        with self.busy:
            self.con.execute("UPDATE encounters SET atlas_drops = ? WHERE atlas_drops IS NULL AND kind != ''", (value,))
            self.con.commit()
            self.estimator.refit()

    def plants_without_atlas(self) -> int:
        return self.con.execute("SELECT COUNT(*) FROM encounters WHERE atlas_drops IS NULL AND kind != '' "
                                "AND status = 'ok'").fetchone()[0]


def selftest(out_path) -> int:
    """Checks that the installed app has everything (run by the build server on the finished
    bundle): writes a JSON report and returns 0 if all is well."""
    import json
    import tempfile
    import urllib.request
    report = {"version": __version__, "frozen": bool(getattr(sys, "frozen", False))}
    ok = True

    def check(name, fn):
        nonlocal ok
        try:
            report[name] = fn() or "ok"
        except Exception as exc:
            ok = False
            report[name] = f"FAILED: {type(exc).__name__}: {exc}"

    from . import reader, model, settings as settings_mod
    check("templates", lambda: str(len(reader._load_templates())))
    check("starter_model", lambda: "loaded" if model.load_starter() else (_ for _ in ()).throw(RuntimeError("missing")))
    check("dashboard_page", lambda: os.path.getsize(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "index.html")))
    check("icons", lambda: [os.path.getsize(asset(f"tray_{s}.ico")) for s in ("ready", "paused", "problem")])

    def ocr():
        from PIL import Image, ImageDraw, ImageFont
        import numpy as np
        exe = reader.find_tesseract()
        if not exe:
            raise RuntimeError("tesseract not found")
        img = Image.new("L", (420, 60), 255)
        d = ImageDraw.Draw(img)
        try:
            f = ImageFont.truetype("arial.ttf", 36)
        except OSError:
            f = ImageFont.load_default()
        d.text((10, 8), "1234 VIVID", fill=0, font=f)
        text = reader.TesseractEngine(exe).read_line(np.array(img))
        if "1234" not in text:
            raise RuntimeError(f"read {text!r}")
        return f"{exe} -> {text!r}"
    check("ocr", ocr)
    check("mss", lambda: __import__("mss") and "ok")
    check("tkinter", lambda: __import__("tkinter").TkVersion)

    def dashboard():
        from . import dashboard_server
        tmp = tempfile.mkdtemp()
        s = settings_mod.Settings(os.path.join(tmp, "settings.json"))
        s.data.update(data_dir=tmp)
        settings_mod.use(s)
        from . import db
        db.connect(s.db_path).close()
        d = dashboard_server.Dashboard(18765).start()
        try:
            with urllib.request.urlopen(d.url + "api/data", timeout=10) as r:
                data = json.loads(r.read())
            return f"{len(data['encounters'])} encounters, model fitted={data['model'].get('fitted')}"
        finally:
            d.stop()
    check("dashboard", dashboard)
    report["ok"] = ok
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog=APP_NAME)
    ap.add_argument("--background", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--selftest", metavar="REPORT.json")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest(args.selftest)

    win.set_dpi_awareness()                          # before anything asks Windows about windows or monitors
    if not win.single_instance():
        win.message_box(f"{APP_NAME} is already running - look for the leaf icon by the clock "
                        "(you may need to click the ^ arrow to see it).")
        return 0
    from . import reader, settings as settings_mod
    settings = settings_mod.current()
    settings.ensure_dirs()
    setup_logging(settings.logs_dir, args.debug)
    try:
        engine = reader.make_engine("tesseract")
    except FileNotFoundError as exc:
        win.message_box(str(exc), warning=True)
        return 1
    try:
        app = App(settings, engine, background=args.background)
        code = app.run()
    except Exception as exc:
        log.exception("couldn't start")
        win.message_box(f"{APP_NAME} couldn't start:\n\n{exc}\n\nDetails are in {settings.logs_dir}\\app.log",
                        warning=True)
        return 1
    if app.restart:
        exe = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "poe_harvest"]
        win.release_single_instance()
        subprocess.Popen(exe)
    return code
