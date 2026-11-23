"""The Harvest database (SQLite): one file, D:\\poe harvest\\data\\harvest.db."""
from __future__ import annotations

import json
import sqlite3

from . import mods as modlib

SCHEMA = """
CREATE TABLE IF NOT EXISTS maps (
    id INTEGER PRIMARY KEY,
    area_id TEXT, instance TEXT, name TEXT, first_seen TEXT,
    UNIQUE(area_id, instance));
CREATE TABLE IF NOT EXISTS mods (
    id TEXT PRIMARY KEY, wording TEXT, kind TEXT,
    icon_override TEXT);                       -- set from the dashboard: good / bad / neutral
CREATE TABLE IF NOT EXISTS encounters (
    id INTEGER PRIMARY KEY,
    map_id INTEGER REFERENCES maps(id),
    kind TEXT,                                  -- flora / specimen
    plant_type TEXT, rarity TEXT, plant_name TEXT,
    node_time TEXT, result_time TEXT,
    lifeforce_total INTEGER,
    rerolls INTEGER DEFAULT 0, chaos_spent INTEGER DEFAULT 0,
    wisps_yellow_node INTEGER DEFAULT 0, wisps_blue_node INTEGER DEFAULT 0, wisps_purple_node INTEGER DEFAULT 0,
    wisps_yellow_result INTEGER DEFAULT 0, wisps_blue_result INTEGER DEFAULT 0, wisps_purple_result INTEGER DEFAULT 0,
    status TEXT,                                -- pending / ok / needs review / unreadable / dropped / no node / excluded
    source TEXT,                                -- live / screenshots
    note TEXT);
CREATE TABLE IF NOT EXISTS encounter_mods (
    encounter_id INTEGER REFERENCES encounters(id), mod_id TEXT, value INTEGER,
    PRIMARY KEY (encounter_id, mod_id));
CREATE TABLE IF NOT EXISTS encounter_rewards (
    encounter_id INTEGER REFERENCES encounters(id), item TEXT, count INTEGER);
CREATE TABLE IF NOT EXISTS lifeforce_stacks (
    encounter_id INTEGER REFERENCES encounters(id), colour TEXT, amount INTEGER);
CREATE TABLE IF NOT EXISTS reads (
    id INTEGER PRIMARY KEY,
    encounter_id INTEGER, key TEXT, time TEXT,
    screenshot TEXT, crop TEXT,                 -- files kept for checking
    parsed TEXT,                                -- what was read, as JSON
    ok INTEGER);
CREATE TABLE IF NOT EXISTS unknown_lines (
    id INTEGER PRIMARY KEY, read_id INTEGER, text TEXT, confirmed_mod_id TEXT);
"""


# columns added after the first release: (table, column, type)
ADDED_COLUMNS = [
    ("encounters", "est", "REAL"),              # phase 4: estimate shown in the popup at the plant
    ("encounters", "est_lo", "REAL"),
    ("encounters", "est_hi", "REAL"),
    ("encounters", "est_first", "REAL"),        # estimate of the plant's first roll (reroll ledger)
    ("encounters", "price", "REAL"),            # chaos per lifeforce of the plant's type, at the result
    ("encounters", "atlas_drops", "REAL"),      # the Atlas Drops % set when the plant was read (app, 22 Nov)
    ("encounters", "tree_lifeforce", "REAL"),   # lifeforce quantity % from atlas passives, set at the time
    ("encounters", "lifeforce_dup", "REAL"),    # atlas passives: % chance for lifeforce to be duplicated
    ("encounters", "fauna_dup", "REAL"),        # atlas passives: % chance for Sacred Fauna to be duplicated
    ("encounters", "white_map", "INTEGER"),     # 1 = a white map (no map mods)
    ("encounters", "area_level", "INTEGER"),    # the map's level, from the game's log
]


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, check_same_thread=False, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode = WAL")       # the dashboard can read while the tracker writes
    except sqlite3.DatabaseError:
        pass
    con.executescript(SCHEMA)
    for table, column, ctype in ADDED_COLUMNS:
        if column not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ctype}")
    # 22 Nov: a plant with no Numpad 3 result is dropped from everything (was "incomplete")
    con.execute("UPDATE encounters SET status = 'dropped' WHERE status = 'incomplete'")
    con.commit()
    for mid, wording, kind in modlib.MODS:
        con.execute("INSERT OR IGNORE INTO mods (id, wording, kind) VALUES (?, ?, ?)", (mid, wording, kind))
    con.commit()
    return con


def map_id(con, area_id: str, instance: str, name: str, when: str) -> int:
    con.execute("INSERT OR IGNORE INTO maps (area_id, instance, name, first_seen) VALUES (?, ?, ?, ?)",
                (area_id or "", instance or "", name, when))
    return con.execute("SELECT id FROM maps WHERE area_id = ? AND instance = ?",
                       (area_id or "", instance or "")).fetchone()[0]


def add_read(con, encounter_id, key, when, screenshot, crop, parsed: dict, ok: bool) -> int:
    cur = con.execute("INSERT INTO reads (encounter_id, key, time, screenshot, crop, parsed, ok) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (encounter_id, key, when, screenshot, crop, json.dumps(parsed), int(ok)))
    return cur.lastrowid


def set_mods(con, encounter_id: int, mod_values: dict, rewards: dict):
    con.execute("DELETE FROM encounter_mods WHERE encounter_id = ?", (encounter_id,))
    con.execute("DELETE FROM encounter_rewards WHERE encounter_id = ?", (encounter_id,))
    con.executemany("INSERT INTO encounter_mods VALUES (?, ?, ?)",
                    [(encounter_id, k, v) for k, v in mod_values.items()])
    con.executemany("INSERT INTO encounter_rewards VALUES (?, ?, ?)",
                    [(encounter_id, k, v) for k, v in rewards.items()])


def set_stacks(con, encounter_id: int, stacks: list):
    con.execute("DELETE FROM lifeforce_stacks WHERE encounter_id = ?", (encounter_id,))
    con.executemany("INSERT INTO lifeforce_stacks VALUES (?, ?, ?)",
                    [(encounter_id, c, a) for a, c in stacks])


def count_ok(con, kind="flora") -> int:
    return con.execute("SELECT COUNT(*) FROM encounters WHERE status = 'ok' AND kind = ?", (kind,)).fetchone()[0]
