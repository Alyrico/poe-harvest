"""
Run the reader over every screenshot in the answer key and compare with the hand-checked answers.

    python check_accuracy.py [screenshots folder] [--engine tesseract|rapidocr]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
from poe_harvest import reader, mods  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default=os.path.join(os.path.dirname(os.path.dirname(HERE)), "screenshots"))
    ap.add_argument("--engine", default="tesseract")
    ap.add_argument("--key", default=os.path.join(HERE, "answer_key.json"))
    ap.add_argument("--height", type=int, default=0,
                    help="first resize every screenshot to this height (e.g. 1080, 2160) to test other resolutions")
    args = ap.parse_args()

    key = {k: v for k, v in json.load(open(args.key)).items() if not k.startswith("_")}
    log = {}
    log_path = os.path.join(args.folder, "capture_log.csv")
    if os.path.exists(log_path):
        log = {r["file"]: r for r in csv.DictReader(open(log_path, newline="", encoding="utf-8"))}
    try:
        engine = reader.make_engine(args.engine)
    except FileNotFoundError as exc:
        print(exc)
        return 1

    checks = fails = 0
    problems, unknown = [], []
    t0 = time.time()
    for f, want in key.items():
        path = os.path.join(args.folder, f)
        if not os.path.exists(path):
            problems.append(f"{f}: file missing")
            continue
        img = cv2.imread(path)
        r = log.get(f)
        cursor = (int(r["cursor_x"]), int(r["cursor_y"])) if r and r.get("cursor_x") else None
        if args.height and img is not None and args.height != img.shape[0]:
            k = args.height / img.shape[0]
            img = cv2.resize(img, (round(img.shape[1] * k), args.height),
                             interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)
            cursor = (cursor[0] * k, cursor[1] * k) if cursor else None
        is_node = f.endswith("_node.png")
        got = reader.read_screenshot(img, engine, cursor,
                                     ("tooltip", "wisps") if is_node else ("lifeforce", "wisps"))

        def check(label, a, b):
            nonlocal checks, fails
            checks += 1
            if a != b:
                fails += 1
                problems.append(f"{f[11:23]} {label}: read {a!r}, expected {b!r}")

        gw = got["wisps"]
        check("wisp colours", sorted(gw), sorted(want["wisps"]))
        for colour, value in gw.items():
            if value is None:
                unknown.append(f"{f[11:23]} {colour} wisps unknown")
            else:
                check(f"{colour} wisps", value, want["wisps"].get(colour))
        if is_node:
            tt = got["tooltip"]
            if want["tooltip"] is None:
                check("tooltip", None if tt is None else "found", None)
                continue
            if tt is None:
                check("tooltip", None, "found")
                continue
            p = mods.parse_tooltip(tt)
            w = want["tooltip"]
            check("kind", p.kind, w["kind"])
            check("rarity", p.rarity, w["rarity"])
            check("name", p.name, w["name"])
            check("colour", p.colour, w["colour"])
            got_mods = {m.id: m.value for m in p.mods if m.kind == "mod"}
            for mid, val in w["mods"].items():
                check(f"mod {mid}", got_mods.get(mid, "MISSING"), val)
            for mid in got_mods:
                if mid not in w["mods"]:
                    check(f"extra mod {mid}", got_mods[mid], "not there")
            got_rewards = {k: v for k, v in p.rewards.items()}
            check("rewards", got_rewards, w["rewards"])
            if p.unknown:
                check("unknown lines", p.unknown, [])
        else:
            got_lf = [[l.amount, l.colour] for l in got["lifeforce"]]
            check("lifeforce", sorted(got_lf, key=str), sorted(want["lifeforce"], key=str))

    print(f"engine: {engine.name}   screenshots: {len(key)}   checks: {checks}   wrong: {fails}"
          f"   time: {time.time() - t0:.0f}s")
    for p in problems:
        print("  ", p)
    if unknown:
        print(f"   marked unknown instead of guessing ({len(unknown)}): " + "; ".join(unknown))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
