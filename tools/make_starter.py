"""
Build the starter model shipped with the app (src/poe_harvest/assets/starter_model.json) from a
harvest.db: new players get estimates from these plants until they have enough of their own.

    python tools/make_starter.py path/to/harvest.db [--atlas 82]

--atlas: the Atlas Drops % for plants saved before the app recorded it.
"""
import argparse
import datetime as dt
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
from poe_harvest import __version__, model as mdl  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--atlas", type=float, default=None)
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    data = mdl.plants(con, default_atlas=args.atlas)
    m = mdl.fit(data)
    if m is None:
        print("not enough plants")
        return 1
    rolls = [{"type": r["type"], "mods": r["mods"], "rewards": r["rewards"]} for r in mdl.rolls(con)]
    out = m.to_json({"version": __version__, "created": dt.date.today().isoformat(),
                     "rolls": rolls, "fingerprint": sorted(mdl.fingerprint(con)),
                     "typical_error": m.loo_abs_err, "baseline_error": m.base_abs_err})
    path = mdl.STARTER_PATH
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"starter model: {m.n} plants, {len(rolls)} rolls, typically {m.loo_abs_err:.0%} off "
          f"(type average {m.base_abs_err:.0%}), variant scaled={m.scaled} curve={m.curve} lambda={m.lam}")
    print("written to", path, os.path.getsize(path), "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
