"""
The settings window (tkinter, on its own thread while open). Opened from the tray icon, and on
first start, when it also walks through the few things that must be set.
"""
from __future__ import annotations

import logging
import os
import queue
import shutil
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import colorchooser, filedialog, messagebox, ttk

from . import __version__, win, zones

log = logging.getLogger(__name__)
PAD = {"padx": 10, "pady": 5}


class SettingsWindow:
    def __init__(self, app, first_run=False, focus=None):
        self.app = app
        self.s = app.settings
        self.first_run = first_run
        self.focus = focus
        self.alive = False
        self.q = queue.Queue()                 # callbacks from other threads, run on the tk thread
        self.root = None

    def start(self):
        self.alive = True
        threading.Thread(target=self._run, name="settings", daemon=True).start()

    def raise_window(self):
        self.q.put(lambda: (self.root.deiconify(), self.root.lift(), self.root.focus_force()))

    # ------------------------------------------------------------------ building
    def _run(self):
        try:
            self._build()
            self.root.mainloop()
        except Exception:
            log.exception("settings window failed")
        finally:
            self.alive = False

    def _poll(self):
        while True:
            try:
                fn = self.q.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                log.exception("settings window callback failed")
        if self.root:
            self.root.after(100, self._poll)

    def _build(self):
        s = self.s
        root = self.root = tk.Tk()
        root.title("PoE Harvest Tracker - Settings")
        try:
            dpi = __import__("ctypes").windll.user32.GetDpiForSystem()
            root.tk.call("tk", "scaling", dpi / 72)
        except Exception:
            pass
        try:
            root.iconbitmap(os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "app.ico"))
        except Exception:
            pass
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Hint.TLabel", foreground="#666666")
        style.configure("Welcome.TLabel", font=("Segoe UI", 11, "bold"))

        outer = ttk.Frame(root, padding=12)
        outer.pack(fill="both", expand=True)
        if self.first_run:
            ttk.Label(outer, text="Welcome to PoE Harvest Tracker!", style="Welcome.TLabel").pack(anchor="w")
            ttk.Label(outer, wraplength=560, justify="left", text=(
                "Check the settings below - at least Atlas Drops and Rich Soil - and set up your loot filter "
                "so lifeforce drops are easy to read (the guide shows how). Then press Save. The app keeps "
                "running in the tray by the clock: press the plant hotkey at a Sacred Flora, and the result "
                "hotkey once its lifeforce has dropped.")).pack(anchor="w", pady=(2, 8))
        nb = ttk.Notebook(outer)
        nb.pack(fill="both", expand=True)
        self.v = {}

        # ---- Game ----
        g = ttk.Frame(nb, padding=8)
        nb.add(g, text="Game")
        r = 0
        ttk.Label(g, text="Atlas Drops %").grid(row=r, column=0, sticky="w", **PAD)
        self.v["atlas"] = tk.StringVar(value="" if s.get("atlas_drops") is None else f"{s.get('atlas_drops'):g}")
        atlas = ttk.Spinbox(g, from_=0, to=500, increment=1, width=8, textvariable=self.v["atlas"])
        atlas.grid(row=r, column=1, sticky="w", **PAD)
        r += 1
        ttk.Label(g, style="Hint.TLabel", wraplength=520, justify="left", text=(
            "The map's Atlas Drops bonus. Set it before running a map with a different bonus: every plant "
            "is saved with the value set when you read it, and the estimate scales with it.")).grid(
            row=r, column=0, columnspan=3, sticky="w", padx=10)
        r += 1
        self.missing_atlas_row = r
        n_missing = self.app.plants_without_atlas()
        if n_missing:
            fr = ttk.Frame(g)
            fr.grid(row=r, column=0, columnspan=3, sticky="w", **PAD)
            top = ttk.Frame(fr)
            top.pack(anchor="w")
            ttk.Label(top, text=f"{n_missing} plants saved earlier have no Atlas Drops %. Give them:").pack(side="left")
            self.v["atlas_old"] = tk.StringVar(value=self.v["atlas"].get() or "0")
            ttk.Spinbox(top, from_=0, to=500, width=6, textvariable=self.v["atlas_old"]).pack(side="left", padx=6)
            ttk.Button(top, text="Apply", command=lambda: self._stamp_atlas(fr)).pack(side="left")
            ttk.Label(fr, style="Hint.TLabel", wraplength=520, justify="left", text=(
                "White (normal) plants from white-map runs get 0%, no tree lifeforce and \"white map\" "
                "instead.")).pack(anchor="w")
            r += 1
        self.v["rich_soil"] = tk.BooleanVar(value=s.get("rich_soil") is not False)
        ttk.Checkbutton(g, text="Rich Soil is allocated on my atlas (Sacred Flora mods 50% stronger)",
                        variable=self.v["rich_soil"]).grid(row=r, column=0, columnspan=3, sticky="w", **PAD)
        r += 1
        ttk.Label(g, text="Atlas tree lifeforce %").grid(row=r, column=0, sticky="w", **PAD)
        self.v["tree"] = tk.StringVar(value=f"{float(s.get('tree_lifeforce') or 0):g}")
        ttk.Spinbox(g, from_=0, to=500, increment=1, width=8, textvariable=self.v["tree"]).grid(
            row=r, column=1, sticky="w", **PAD)
        r += 1
        ttk.Label(g, text="Lifeforce duplicate chance %").grid(row=r, column=0, sticky="w", **PAD)
        self.v["lf_dup"] = tk.StringVar(value=f"{float(s.get('lifeforce_dup') or 0):g}")
        ttk.Spinbox(g, from_=0, to=100, increment=1, width=8, textvariable=self.v["lf_dup"]).grid(
            row=r, column=1, sticky="w", **PAD)
        r += 1
        ttk.Label(g, text="Sacred Fauna duplicate chance %").grid(row=r, column=0, sticky="w", **PAD)
        self.v["fauna_dup"] = tk.StringVar(value=f"{float(s.get('fauna_dup') or 0):g}")
        ttk.Spinbox(g, from_=0, to=100, increment=1, width=8, textvariable=self.v["fauna_dup"]).grid(
            row=r, column=1, sticky="w", **PAD)
        r += 1
        ttk.Label(g, style="Hint.TLabel", wraplength=520, justify="left", text=(
            "From your atlas passive tree's stat list (0 if you don't have them): \"Sacred Fauna in your Maps drop "
            "X% increased Quantity of Lifeforce\", \"Lifeforce dropped by Sacred Fauna ... has X% chance to be "
            "Duplicated\" and \"Sacred Fauna ... have X% chance to be duplicated\". Saved with each plant, like "
            "Atlas Drops.")).grid(row=r, column=0, columnspan=3, sticky="w", padx=10)
        r += 1
        self.v["white"] = tk.BooleanVar(value=bool(s.get("white_map")))
        ttk.Checkbutton(g, text="White map (no map mods) - for baseline tests; also in the tray menu",
                        variable=self.v["white"]).grid(row=r, column=0, columnspan=3, sticky="w", **PAD)
        r += 1
        ttk.Label(g, text="Tier 3 fauna per plant").grid(row=r, column=0, sticky="w", **PAD)
        self.v["t3"] = tk.StringVar(value="" if s.get("t3_base") is None else f"{float(s.get('t3_base')):g}")
        ttk.Spinbox(g, from_=0, to=50, increment=0.5, width=8, textvariable=self.v["t3"]).grid(
            row=r, column=1, sticky="w", **PAD)
        r += 1
        ttk.Label(g, style="Hint.TLabel", wraplength=520, justify="left", text=(
            "How many Tier 3 Sacred Fauna a plant spawns without a \"+ Rare Tier 3\" mod. Used to predict "
            "Stacked Decks from the deck mod. The Tier 3 chance nodes make it vary, so count a few plants and "
            "enter the average (e.g. 4.5). Leave empty until you've counted.")).grid(
            row=r, column=0, columnspan=3, sticky="w", padx=10)
        r += 1
        ttk.Label(g, text="Path of Exile folder").grid(row=r, column=0, sticky="w", **PAD)
        self.v["poe"] = tk.StringVar(value=s.get("poe_log") or "")
        ttk.Entry(g, textvariable=self.v["poe"], width=44).grid(row=r, column=1, sticky="we", **PAD)
        ttk.Button(g, text="Browse...", command=self._browse_poe).grid(row=r, column=2, **PAD)
        r += 1
        found = zones.find_game_log(s.get("poe_log") or "")
        self.poe_found = ttk.Label(g, style="Hint.TLabel", wraplength=520, text=(
            f"Leave empty to find it automatically. Using: {found}" if found else
            "Not found yet - start the game, or pick the folder with PathOfExile.exe (standalone or Steam). "
            "Without the game's log every key press is read, not only in maps."))
        self.poe_found.grid(row=r, column=0, columnspan=3, sticky="w", padx=10)
        r += 1
        ttk.Label(g, text="Lifeforce label colour").grid(row=r, column=0, sticky="w", **PAD)
        self.label_colour = list(s.get("label_colour") or [255, 0, 255])
        self.swatch = tk.Label(g, width=4, relief="groove", bg=self._hex(self.label_colour))
        self.swatch.grid(row=r, column=1, sticky="w", **PAD)
        ttk.Button(g, text="Pick...", command=self._pick_colour).grid(row=r, column=2, **PAD)
        r += 1
        fr = ttk.Frame(g)
        fr.grid(row=r, column=0, columnspan=3, sticky="w", **PAD)
        ttk.Button(fr, text="Loot filter guide", command=lambda: webbrowser.open(self.app_guide())).pack(side="left")
        ttk.Button(fr, text="Test: read the lifeforce on screen in 5 s", command=self._test_labels).pack(side="left", padx=8)
        r += 1
        self.test_result = ttk.Label(g, style="Hint.TLabel", wraplength=520, text="")
        self.test_result.grid(row=r, column=0, columnspan=3, sticky="w", padx=10)
        g.columnconfigure(1, weight=1)

        # ---- Hotkeys & popup ----
        h = ttk.Frame(nb, padding=8)
        nb.add(h, text="Hotkeys and popup")
        self.hotkeys = s.get("hotkeys")
        self.hk_labels = {}
        for i, (key, text) in enumerate((("node", "Read the plant"), ("result", "Read the lifeforce drop"))):
            ttk.Label(h, text=text).grid(row=i, column=0, sticky="w", **PAD)
            lab = ttk.Label(h, text=win.hotkey_name(self.hotkeys.get(key)), width=28, relief="sunken", anchor="center")
            lab.grid(row=i, column=1, sticky="w", **PAD)
            self.hk_labels[key] = lab
            ttk.Button(h, text="Change...", command=lambda k=key: self._capture(k)).grid(row=i, column=2, **PAD)
        ttk.Label(h, style="Hint.TLabel", wraplength=520, justify="left", text=(
            "Click Change, then press a key or a mouse side button (Esc cancels). Keys only count while "
            "Path of Exile is the active window and you're in a map, so typing elsewhere is never read.")
        ).grid(row=2, column=0, columnspan=3, sticky="w", padx=10)
        self.v["sounds"] = tk.BooleanVar(value=bool(s.get("sounds")))
        ttk.Checkbutton(h, text="Sounds", variable=self.v["sounds"]).grid(row=3, column=0, sticky="w", **PAD)
        self.v["volume"] = tk.IntVar(value=int(s.get("volume") or 70))
        ttk.Scale(h, from_=0, to=100, variable=self.v["volume"], length=200).grid(row=3, column=1, sticky="w", **PAD)
        ttk.Button(h, text="Test", command=self._test_sound).grid(row=3, column=2, **PAD)
        ttk.Label(h, text="Popup size").grid(row=4, column=0, sticky="w", **PAD)
        self.v["popup"] = tk.IntVar(value=int(round(100 * float(s.get("popup_scale") or 1))))
        ps = ttk.Frame(h)
        ps.grid(row=4, column=1, sticky="w", **PAD)
        ttk.Scale(ps, from_=60, to=160, variable=self.v["popup"], length=200,
                  command=lambda _=None: self.popup_pct.config(text=f"{self.v['popup'].get()}%")).pack(side="left")
        self.popup_pct = ttk.Label(ps, text=f"{self.v['popup'].get()}%", width=5)
        self.popup_pct.pack(side="left", padx=6)

        # ---- Data ----
        d = ttk.Frame(nb, padding=8)
        nb.add(d, text="Data")
        ttk.Label(d, text="Data folder").grid(row=0, column=0, sticky="w", **PAD)
        self.v["data"] = tk.StringVar(value=s.data_dir)
        ttk.Entry(d, textvariable=self.v["data"], width=44).grid(row=0, column=1, sticky="we", **PAD)
        ttk.Button(d, text="Browse...", command=self._browse_data).grid(row=0, column=2, **PAD)
        ttk.Label(d, style="Hint.TLabel", wraplength=520, justify="left", text=(
            "The database, cut-outs of everything read, corrections and logs. Changing it offers to copy "
            "your data over, then restarts the app.")).grid(row=1, column=0, columnspan=3, sticky="w", padx=10)
        self.v["shots"] = tk.BooleanVar(value=bool(s.get("save_screenshots")))
        ttk.Checkbutton(d, text="Save full screenshots (about 6 MB each; only needed to re-read history later)",
                        variable=self.v["shots"]).grid(row=2, column=0, columnspan=3, sticky="w", **PAD)
        fr = ttk.Frame(d)
        fr.grid(row=3, column=0, columnspan=3, sticky="w", **PAD)
        ttk.Label(fr, text="Delete screenshots older than").pack(side="left")
        self.v["keep"] = tk.StringVar(value=str(int(s.get("keep_screenshots_days") or 0)))
        ttk.Spinbox(fr, from_=0, to=3650, width=6, textvariable=self.v["keep"]).pack(side="left", padx=6)
        ttk.Label(fr, text="days (0 = never)").pack(side="left")
        ttk.Label(d, text="Screenshots folder").grid(row=4, column=0, sticky="w", **PAD)
        self.v["shots_dir"] = tk.StringVar(value=s.get("screenshots_dir") or "")
        ttk.Entry(d, textvariable=self.v["shots_dir"], width=44).grid(row=4, column=1, sticky="we", **PAD)
        ttk.Button(d, text="Browse...", command=self._browse_shots).grid(row=4, column=2, **PAD)
        ttk.Label(d, style="Hint.TLabel", text="Empty = a 'screenshots' folder inside the data folder.").grid(
            row=5, column=0, columnspan=3, sticky="w", padx=10)
        fr = ttk.Frame(d)
        fr.grid(row=6, column=0, columnspan=3, sticky="w", **PAD)
        ttk.Button(fr, text="Open data folder", command=lambda: os.startfile(s.data_dir)).pack(side="left")
        ttk.Button(fr, text="Rebuild database from saved screenshots...", command=self._rebuild).pack(side="left", padx=8)
        self.rebuild_status = ttk.Label(d, style="Hint.TLabel", wraplength=520, text="")
        self.rebuild_status.grid(row=7, column=0, columnspan=3, sticky="w", padx=10)
        d.columnconfigure(1, weight=1)

        # ---- App ----
        a = ttk.Frame(nb, padding=8)
        nb.add(a, text="App")
        self.v["start"] = tk.BooleanVar(value=bool(s.get("start_with_windows")))
        ttk.Checkbutton(a, text="Start with Windows (in the tray)", variable=self.v["start"]).grid(
            row=0, column=0, columnspan=3, sticky="w", **PAD)
        self.v["updates"] = tk.BooleanVar(value=bool(s.get("check_updates")))
        ttk.Checkbutton(a, text="Tell me when there's a new version (checks GitHub once a day; never installs by itself)",
                        variable=self.v["updates"]).grid(row=1, column=0, columnspan=3, sticky="w", **PAD)
        ttk.Label(a, text="Lifeforce prices league").grid(row=2, column=0, sticky="w", **PAD)
        self.v["league"] = tk.StringVar(value=s.get("league") or "")
        ttk.Entry(a, textvariable=self.v["league"], width=24).grid(row=2, column=1, sticky="w", **PAD)
        ttk.Label(a, style="Hint.TLabel", text="Empty = the current challenge league on poe.ninja "
                                               "(e.g. type 'Hardcore Covens' for hardcore).").grid(
            row=3, column=0, columnspan=3, sticky="w", padx=10)
        ttk.Label(a, text="Starter estimates").grid(row=4, column=0, sticky="w", **PAD)
        self.v["starter"] = tk.StringVar(value=s.get("starter_estimates") or "auto")
        ttk.Combobox(a, textvariable=self.v["starter"], values=("auto", "on", "off"), state="readonly",
                     width=8).grid(row=4, column=1, sticky="w", **PAD)
        ttk.Label(a, style="Hint.TLabel", wraplength=520, justify="left", text=(
            "auto: estimates start from the author's plants until you have 100 of your own, which then take "
            "over. off: no estimates until your own plants are enough.")).grid(
            row=5, column=0, columnspan=3, sticky="w", padx=10)
        ttk.Label(a, text="Dashboard port").grid(row=6, column=0, sticky="w", **PAD)
        self.v["port"] = tk.StringVar(value=str(s.get("port")))
        ttk.Entry(a, textvariable=self.v["port"], width=8).grid(row=6, column=1, sticky="w", **PAD)
        ttk.Label(a, style="Hint.TLabel", text=f"PoE Harvest Tracker {__version__} - settings in {s.path}").grid(
            row=7, column=0, columnspan=3, sticky="w", padx=10, pady=(16, 0))

        # ---- buttons ----
        b = ttk.Frame(outer)
        b.pack(fill="x", pady=(10, 0))
        ttk.Button(b, text="Save", command=self._save).pack(side="right")
        ttk.Button(b, text="Cancel", command=self._close).pack(side="right", padx=8)
        root.protocol("WM_DELETE_WINDOW", self._close)
        if self.focus == "atlas" or self.first_run:
            atlas.focus_set()
            atlas.selection_range(0, "end")
        root.after(100, self._poll)
        root.lift()
        root.attributes("-topmost", True)
        root.after(300, lambda: root.attributes("-topmost", False))
        root.focus_force()

    # ------------------------------------------------------------------ helpers
    def app_guide(self):
        from .app import GUIDE_URL
        return GUIDE_URL

    @staticmethod
    def _hex(rgb):
        return "#%02x%02x%02x" % tuple(int(c) for c in rgb)

    def _pick_colour(self):
        c = colorchooser.askcolor(color=self._hex(self.label_colour), parent=self.root,
                                  title="The background colour of lifeforce labels in your loot filter")
        if c and c[0]:
            self.label_colour = [int(x) for x in c[0]]
            self.swatch.config(bg=self._hex(self.label_colour))

    def _browse_poe(self):
        d = filedialog.askdirectory(parent=self.root, title="The Path of Exile folder (with PathOfExile.exe)")
        if d:
            self.v["poe"].set(os.path.normpath(d))
            found = zones.find_game_log(os.path.normpath(d))
            self.poe_found.config(text=f"Using: {found}" if found else "No logs\\Client.txt in that folder.")

    def _browse_data(self):
        d = filedialog.askdirectory(parent=self.root, title="Where to keep PoE Harvest's data")
        if d:
            self.v["data"].set(os.path.normpath(d))

    def _browse_shots(self):
        d = filedialog.askdirectory(parent=self.root, title="Where to keep full screenshots")
        if d:
            self.v["shots_dir"].set(os.path.normpath(d))

    def _capture(self, key):
        self.hk_labels[key].config(text="Press a key or mouse button... (Esc = cancel)")

        def got(hk):
            def apply():
                if hk:
                    self.hotkeys[key] = hk
                self.hk_labels[key].config(text=win.hotkey_name(self.hotkeys.get(key)))
            self.q.put(apply)
        self.app.ui.capture_next_key(got)

    def _test_sound(self):
        win.sound_settings.update(on=True, volume=int(self.v["volume"].get()))
        win.play("result")
        win.sound_settings.update(on=bool(self.s.get("sounds")), volume=int(self.s.get("volume")))

    def _stamp_atlas(self, frame):
        try:
            value = float(self.v["atlas_old"].get())
        except ValueError:
            messagebox.showerror("Atlas Drops", "Type a number, e.g. 82.", parent=self.root)
            return
        self.app.stamp_missing_atlas(value)
        for w in frame.winfo_children():
            w.destroy()
        ttk.Label(frame, text=f"Done - earlier plants saved with {value:g}%.").pack(side="left")

    def _test_labels(self):
        """One screen read of the game window, 5 seconds after the button press."""
        self.test_result.config(text="Switch to the game with a lifeforce drop on screen...")

        def run():
            time.sleep(5)
            try:
                import mss
                import numpy as np
                from . import reader
                hwnd = win.find_poe_window()
                region = win.poe_region(hwnd) if hwnd else None
                if not region:
                    msg = "Path of Exile isn't open (or is minimised)."
                else:
                    cap = mss.MSS if hasattr(mss, "MSS") else mss.mss
                    with cap() as sct:
                        raw = np.ascontiguousarray(np.array(sct.grab(region))[:, :, :3])
                    res = reader.read_screenshot(raw, self.app.engine, None, ("lifeforce",),
                                                 label_colour=tuple(self.label_colour))
                    labels = res["lifeforce"]
                    if not labels:
                        msg = ("No lifeforce labels found. Check the label colour above matches your loot filter, "
                               "and that the drop is on screen.")
                    else:
                        msg = "Found: " + ", ".join(f"{l.amount if l.amount is not None else '?'} "
                                                     f"{l.colour or '(unreadable)'}" for l in labels)
            except Exception as exc:
                msg = f"Test failed: {exc}"
            self.q.put(lambda: self.test_result.config(text=msg))
        threading.Thread(target=run, daemon=True).start()

    def _rebuild(self):
        if not os.path.isfile(os.path.join(self.s.screenshots_dir, "capture_log.csv")):
            messagebox.showinfo("Rebuild", "There are no saved screenshots to re-read (turn on 'Save full "
                                           "screenshots' to keep them from now on).", parent=self.root)
            return
        if not messagebox.askyesno("Rebuild database", (
                "Re-read every saved screenshot with the current reader into a fresh database?\n\n"
                "Your current database is kept as a backup, and corrections made in the dashboard are "
                "applied again. Reading pauses until it's done (about 1 second per screenshot)."), parent=self.root):
            return

        def progress(i, n):
            self.q.put(lambda: self.rebuild_status.config(text=f"Re-reading {i + 1} of {n}..."))

        def run():
            try:
                summary = self.app.rebuild(progress)
            except Exception as exc:
                log.exception("rebuild failed")
                summary = f"Rebuild failed: {exc}"
            self.q.put(lambda: self.rebuild_status.config(text=summary))
        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------------ save / close
    def _save(self):
        s = self.s
        try:
            atlas_txt = self.v["atlas"].get().strip()
            atlas = float(atlas_txt) if atlas_txt else None
            port = int(self.v["port"].get())
            keep = int(self.v["keep"].get() or 0)
            tree = float(self.v["tree"].get().strip() or 0)
            lf_dup = float(self.v["lf_dup"].get().strip() or 0)
            fauna_dup = float(self.v["fauna_dup"].get().strip() or 0)
            if not (0 <= lf_dup <= 100 and 0 <= fauna_dup <= 100):
                raise ValueError("Duplicate chances should be between 0 and 100")
            t3_txt = self.v["t3"].get().strip()
            t3 = float(t3_txt) if t3_txt else None           # an average over a few plants, e.g. 4.5
            if atlas is not None and not 0 <= atlas <= 1000:
                raise ValueError("Atlas Drops should be between 0 and 1000")
            if not 0 <= tree <= 1000:
                raise ValueError("Atlas tree lifeforce should be between 0 and 1000")
            if t3 is not None and not 0 <= t3 <= 50:
                raise ValueError("Tier 3 fauna per plant should be between 0 and 50")
            if not 1024 <= port <= 65535:
                raise ValueError("The dashboard port should be between 1024 and 65535")
        except ValueError as exc:
            messagebox.showerror("Settings", str(exc) if "should" in str(exc) else "Check the numbers.", parent=self.root)
            return
        if atlas is None:
            if not messagebox.askyesno("Atlas Drops", "Atlas Drops isn't set. Use 0% for now?", parent=self.root):
                return
            atlas = 0.0
        new_data = os.path.normpath(self.v["data"].get().strip() or s.data_dir)
        restart = False
        changes = dict(atlas_drops=atlas, rich_soil=bool(self.v["rich_soil"].get()),
                       tree_lifeforce=tree, lifeforce_dup=lf_dup, fauna_dup=fauna_dup,
                       white_map=bool(self.v["white"].get()), t3_base=t3,
                       poe_log=self.v["poe"].get().strip(), label_colour=self.label_colour,
                       hotkeys=self.hotkeys, sounds=bool(self.v["sounds"].get()), volume=int(self.v["volume"].get()),
                       popup_scale=round(self.v["popup"].get() / 100, 2), save_screenshots=bool(self.v["shots"].get()),
                       keep_screenshots_days=keep, screenshots_dir=self.v["shots_dir"].get().strip(),
                       start_with_windows=bool(self.v["start"].get()), check_updates=bool(self.v["updates"].get()),
                       league=self.v["league"].get().strip(), starter_estimates=self.v["starter"].get(),
                       first_run_done=True)
        if port != s.get("port"):
            changes["port"] = port
            restart = True
        if os.path.normcase(new_data) != os.path.normcase(s.data_dir):
            if not self._move_data(s.data_dir, new_data):
                return
            changes["data_dir"] = new_data
            restart = True
        s.update(**changes)
        log.info("settings saved")
        self._close()
        if restart:
            win.message_box("PoE Harvest Tracker will restart to use the new settings.")
            self.app.quit(restart=True)

    def _move_data(self, old, new) -> bool:
        has_data = os.path.isfile(os.path.join(new, "harvest.db"))
        if has_data:
            return messagebox.askyesno("Data folder", f"{new} already has PoE Harvest data. Use it?", parent=self.root)
        if not os.path.isfile(os.path.join(old, "harvest.db")):
            return True
        if not messagebox.askyesno("Data folder", f"Copy your data from\n{old}\nto\n{new}?\n\n"
                                                  "(The old folder is left as it is.)", parent=self.root):
            return messagebox.askyesno("Data folder", "Start with an empty database in the new folder?", parent=self.root)
        try:
            os.makedirs(new, exist_ok=True)
            with self.app.busy:
                self.app.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                for name in os.listdir(old):
                    src, dst = os.path.join(old, name), os.path.join(new, name)
                    if name.endswith(("-wal", "-shm")):
                        continue
                    if os.path.isdir(src):
                        shutil.copytree(src, dst, dirs_exist_ok=True)
                    else:
                        shutil.copy2(src, dst)
            return True
        except OSError as exc:
            messagebox.showerror("Data folder", f"Couldn't copy the data: {exc}", parent=self.root)
            return False

    def _close(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        self.root = None
        self.alive = False
