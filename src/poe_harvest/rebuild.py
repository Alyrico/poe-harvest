"""
Rebuild the database from all saved screenshots with the current reader (Settings > Advanced).

Use it after the reader has been improved, to re-read your history. Needs the full screenshots
("Save full screenshots" on); where a screenshot is damaged or gone, the saved cut-out is used.
The current database is kept as a backup first (harvest_backup_<date-time>.db).
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime

from . import db as dblib, importer

log = logging.getLogger(__name__)

# only known at the time, can't be re-read from a screenshot
KEEP = ("price", "est", "est_lo", "est_hi", "est_first", "atlas_drops", "tree_lifeforce", "lifeforce_dup",
        "fauna_dup", "white_map", "area_level")


def carry_over(con, backup_path):
    """Keep what the live tracker knew at the time (Atlas Drops %, lifeforce price, estimates shown)
    for plants that are re-read: matched by the screenshot of their Numpad 3 press."""
    try:
        old = dblib.connect(backup_path)
    except Exception:
        return
    have = {r[1] for r in old.execute("PRAGMA table_info(encounters)")}
    cols = [c for c in KEEP if c in have]
    if not cols:
        return
    by_shot = {}
    for row in old.execute(f"""SELECT r.screenshot, {', '.join('e.' + c for c in cols)} FROM reads r
                               JOIN encounters e ON e.id = r.encounter_id WHERE r.key = 'result' AND r.screenshot != ''"""):
        by_shot[row[0]] = row[1:]
    for shot, eid in con.execute("SELECT screenshot, encounter_id FROM reads WHERE key = 'result' AND screenshot != ''").fetchall():
        vals = by_shot.get(shot)
        if vals and any(v is not None for v in vals):
            con.execute(f"UPDATE encounters SET {', '.join(c + ' = COALESCE(' + c + ', ?)' for c in cols)} WHERE id = ?",
                        (*vals, eid))
    con.commit()
    old.close()


def rebuild(settings, engine, progress=None) -> str:
    """Returns a one-line summary. The caller must have closed its own database connection."""
    data = settings.data_dir
    db_path = settings.db_path
    backup = None
    if os.path.exists(db_path):
        backup = os.path.join(data, f"harvest_backup_{datetime.now():%Y-%m-%d_%H-%M-%S}.db")
        shutil.move(db_path, backup)
        log.info("old database kept as %s", backup)
    con = dblib.connect(db_path)
    t0 = time.time()
    fixes = importer.load_fixes(os.path.join(data, "manual_fixes.json"))
    out = importer.import_folder(settings.screenshots_dir, con, engine, fixes, quiet=True,
                                 reads_dir=settings.reads_dir, label_colour=tuple(settings["label_colour"]),
                                 progress=progress)
    if backup:
        carry_over(con, backup)
    importer.export_csv(con, os.path.join(data, "encounters.csv"))
    counts = dict(con.execute("SELECT status, COUNT(*) FROM encounters GROUP BY status").fetchall())
    con.close()
    return (f"Re-read {len(out)} screenshots in {time.time() - t0:.0f}s. Database now holds: "
            + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
