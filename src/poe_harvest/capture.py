"""
What happens on a hotkey press: one screenshot of the game window, read it, update the database,
show the popup, play the sound, keep the evidence. Runs on the worker thread.

    plant hotkey   (Numpad 1) at a plant       -> reads it, popup, one high beep
    result hotkey  (Numpad 3) at the lifeforce -> reads the amount, saves the encounter, two rising beeps
    low buzz                                   -> something was there but couldn't be read: press again
    silent                                     -> nothing to read (or not in a map / the game isn't the active window)

One screenshot per key press. Never sends input to the game, never reads its memory.
"""
from __future__ import annotations

import csv
import logging
import os
import time

import cv2
import numpy as np

from . import mods, popup_render, reader, tracker_core, win, zones

log = logging.getLogger(__name__)
LOG_FIELDS = ["time", "key", "file", "area", "left", "top", "width", "height", "cursor_x", "cursor_y", "atlas"]


def save_png(path, img):
    """Write to a temporary file and then swap it in, so a file is never half-written."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ok, data = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    if not ok:
        return
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(data.tobytes())
    os.replace(tmp, path)


def append_capture_log(folder, row):
    path = os.path.join(folder, "capture_log.csv")
    os.makedirs(folder, exist_ok=True)
    new = not os.path.exists(path)
    try:
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=LOG_FIELDS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow(row)
    except PermissionError:
        log.warning("capture_log.csv is open in another program - row not added")


def clean_old_screenshots(folder, days):
    """Full screenshots older than `days` are deleted (cut-outs are kept forever)."""
    if not days or not os.path.isdir(folder):
        return 0
    cutoff = time.time() - days * 86400
    n = 0
    for root, _, files in os.walk(folder):
        for f in files:
            if f.endswith(".png"):
                p = os.path.join(root, f)
                try:
                    if os.path.getmtime(p) < cutoff:
                        os.remove(p)
                        n += 1
                except OSError:
                    pass
    return n


class Capturer:
    def __init__(self, settings, engine, tracker: tracker_core.EncounterTracker, zone_tracker, ui):
        self.settings = settings
        self.engine = engine
        self.tracker = tracker
        self.zones = zone_tracker
        self.ui = ui
        self.ignored = 0
        self.last_status = "Ready"

    def keep_miss(self, stamp, img):
        """A press that found nothing: keep the screenshot (if screenshots are kept) to check later."""
        if self.settings["save_screenshots"]:
            save_png(os.path.join(self.settings.screenshots_dir, "misses", stamp + ".png"), img)

    def handle(self, sct, key, when, cursor, fg):
        game = win.find_poe_window()
        if not game or fg != game:
            return                                   # typing in another program: stay silent
        area, seed = self.zones.current()
        if self.zones.path and not zones.in_map(area):
            self.ignored += 1
            log.info("ignored %s - not in a map (%s)", key, zones.pretty(area))
            return
        region = win.poe_region(game)
        if region is None:
            return
        shot = sct.grab(region)                      # the one screen read for this key press
        raw = np.ascontiguousarray(np.array(shot)[:, :, :3])
        cur = (cursor[0] - region["left"], cursor[1] - region["top"])
        stamp = f"{when:%Y-%m-%d_%H-%M-%S}-{when.microsecond // 1000:03d}_{key}"
        crop_path = os.path.join(self.settings.reads_dir, f"{when:%Y-%m-%d}", stamp + ".png")
        keep_full = bool(self.settings["save_screenshots"])
        full_name = stamp + ".png" if keep_full else ""
        atlas = self.settings.get("atlas_drops")
        t0 = time.time()

        if key == "node":
            res = reader.read_screenshot(raw, self.engine, cur, ("tooltip", "wisps"))
            img, scale = res["image"], res["scale"]
            tt = res["tooltip"]
            if tt is None:
                log.info("plant key - no plant tooltip on screen")
                self.keep_miss(stamp, raw)
                return                               # nothing to read: stay silent
            parsed = mods.parse_tooltip(tt)
            box = (tt.box[0], tt.box[1], tt.box[2], tt.bottom)
            ev = tracker_core.NodeEvent(when, area, seed, parsed, res["wisps"], box, full_name, crop_path, atlas)
            out = self.tracker.node(ev)
            crop = img[box[1]:box[3] + 12, max(0, box[0]):box[2]]
        else:
            res = reader.read_screenshot(raw, self.engine, cur, ("lifeforce", "wisps"),
                                         label_colour=tuple(self.settings["label_colour"]))
            img, scale = res["image"], res["scale"]
            labels = res["lifeforce"]
            if not labels:
                log.info("result key - no lifeforce labels on screen")
                self.keep_miss(stamp, raw)
                return
            box = (min(l.x0 for l in labels), min(l.y0 for l in labels),
                   max(l.x1 for l in labels), max(l.y1 for l in labels))
            stacks = [(l.amount, l.colour) for l in labels]
            ev = tracker_core.ResultEvent(when, area, seed, stacks, res["wisps"], box, full_name, crop_path)
            out = self.tracker.result(ev)
            crop = img[box[1]:box[3], box[0]:box[2]]

        if out.popup:
            # positions are in the 1440-tall frame: back to screen pixels for the popup
            anchor = out.popup.get("anchor")
            if anchor:
                anchor = tuple(int(v / scale) for v in anchor)
            size = float(self.settings.get("popup_scale") or 1.0) * region["height"] / reader.REFERENCE_HEIGHT
            pic = popup_render.render(out.popup, max(0.5, size))
            x, y = popup_render.place(pic.size, anchor, (region["left"], region["top"], region["width"], region["height"]))
            self.ui.show(pic, x, y, game)
        win.play(out.sound)
        self.last_status = out.log
        log.info("%s   (%.2fs)", out.log, time.time() - t0)

        # keep the evidence (after the popup, so it doesn't slow it down)
        if crop.size:
            save_png(crop_path, crop)
        if keep_full:
            folder = self.settings.screenshots_dir
            save_png(os.path.join(folder, full_name), raw)
            append_capture_log(folder, {"time": when.isoformat(timespec="milliseconds"), "key": key,
                                        "file": full_name, "area": area or "", "left": region["left"],
                                        "top": region["top"], "width": region["width"], "height": region["height"],
                                        "cursor_x": cur[0], "cursor_y": cur[1],
                                        "atlas": "" if atlas is None else atlas})
