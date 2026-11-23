"""
Quick checks that run without screenshots (the build server runs these before packaging).
The reader itself is checked with tools/check_accuracy.py against real screenshots.

    python tools/test_app.py
"""
import math
import os
import random
import sys
import tempfile

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from poe_harvest import model as mdl, mods, reader, settings as settings_mod  # noqa: E402

failures = []


def test(fn):
    try:
        fn()
        print(f"ok    {fn.__name__}")
    except Exception as exc:
        failures.append(fn.__name__)
        print(f"FAIL  {fn.__name__}: {type(exc).__name__}: {exc}")
    return fn


def synthetic(n=150, atlas_levels=(82,), seed=1, white_share=0.0):
    """Plants with known effects: +40% for extra_rare_t3, -20% for spawn_rate, x(1+atlas),
    x0.7 in white maps; no-effect mods (experience, rarity, ...) really do nothing."""
    rnd = random.Random(seed)
    base = {"Wild": 500, "Vivid": 550, "Primal": 520}
    pool = [m for m in mdl.MOD_IDS if m not in ("items_duplicated",)]
    out = []
    for i in range(n):
        t = rnd.choice(mdl.TYPES)
        chosen = rnd.sample(pool, 4)
        ms = {m: (int(mdl.typical_value(m)) if mdl.typical_value(m) else None) for m in chosen}
        a = rnd.choice(atlas_levels)
        white = rnd.random() < white_share
        lf = base[t] * (1 + a / 100) * (0.7 if white else 1)
        lf *= 1.4 if "extra_rare_t3" in ms else 1
        lf *= 0.8 if "spawn_rate" in ms else 1
        lf *= math.exp(rnd.gauss(0, 0.1))
        out.append({"id": i, "type": t, "mods": ms, "rewards": {}, "yellow": 0, "atlas": a, "tree": 0,
                    "white": white, "lifeforce": lf})
    return out


@test
def model_recovers_known_effects():
    m = mdl.fit(synthetic())
    good = m.mod_effect("extra_rare_t3", mdl.typical_value("extra_rare_t3"))[0]
    bad = m.mod_effect("spawn_rate", mdl.typical_value("spawn_rate"))[0]
    assert good > 0.25, good
    assert bad < -0.1, bad
    assert m.loo_abs_err < m.base_abs_err


@test
def no_effect_mods_count_as_nothing():
    m = mdl.fit(synthetic())
    for mid in ("experience", "item_rarity", "wisps_released", "wisp_monsters_after",
                "items_fully_linked", "items_max_sockets", "reward:Stacked Deck"):
        assert m.mod_effect(mid, 100) is None, mid
    plant = {"type": "Vivid", "mods": {"experience": 150, "item_rarity": 80, "items_fully_linked": None},
             "rewards": {"Stacked Deck": 2}, "yellow": 0, "atlas": 82, "tree": 0, "white": False}
    assert abs(m.estimate(plant)[0] / m.base("Vivid", plant) - 1) < 1e-9
    # the base (no lifeforce mods) should be close to the true 550 x 1.82
    assert abs(m.base("Vivid", {"atlas": 82}) / (550 * 1.82) - 1) < 0.15, m.base("Vivid", {"atlas": 82})


@test
def atlas_straight_line_until_measured():
    m = mdl.fit(synthetic(atlas_levels=(82,)))
    assert abs(m.condition_effect(mdl.ATLAS, 82)[0] - 0.82) < 0.05, m.condition_effect(mdl.ATLAS, 82)
    m2 = mdl.fit(synthetic(n=300, atlas_levels=(0, 40, 82)))
    assert m2.counts["atlas_levels"] == 3
    assert abs(m2.condition_effect(mdl.ATLAS, 82)[0] - 0.82) < 0.2, m2.condition_effect(mdl.ATLAS, 82)


@test
def white_maps_are_measured():
    m = mdl.fit(synthetic(n=300, white_share=0.3))
    e = m.condition_effect(mdl.WHITE, True)[0]
    assert -0.4 < e < -0.2, e
    assert m.counts["white_plants"] > 50


@test
def decks_from_the_mods():
    plant = {"mods": {"extra_rare_t3": 6, "items_duplicated": None}, "rewards": {"Stacked Deck": 2}}
    assert mdl.reward_counts(plant, 4) == {"Stacked Deck": 40}          # (4 + 6) x 2 per fauna x duplicated
    assert mdl.reward_counts(plant, None) == {"Stacked Deck": None}     # Tier 3 count not set yet
    assert mdl.reward_counts({"mods": {}, "rewards": {"Stacked Deck": 1}}, 5) == {"Stacked Deck": 5}


@test
def popup_values_decks_and_skips_no_effect_mods():
    from poe_harvest import db as dblib, estimator
    d = tempfile.mkdtemp()
    s = settings_mod.Settings(os.path.join(d, "settings.json"))
    s.update(atlas_drops=82.0, t3_base=4, starter_estimates="off")
    con = dblib.connect(os.path.join(d, "harvest.db"))
    est = estimator.Estimator(con, d, fetch_prices=False, settings=s)
    est.model = mdl.fit(synthetic())
    est.rolls = [dict(p, rewards={"Stacked Deck": 1} if i % 5 == 0 else {}) for i, p in enumerate(synthetic(n=60, seed=5))]
    est.prices.cache = {"prices": {"Vivid": 0.01, "Wild": 0.01, "Primal": 0.01, "Stacked Deck": 3.5}}
    out = est.describe("Vivid", {"experience": 150, "extra_rare_t3": 6}, {"Stacked Deck": 2}, 0)
    assert out["decks"] == 20, out["decks"]          # (4 + 6) Tier 3 fauna x 2 decks each
    lines = {l["text"]: l for l in out["lines"]}
    xp = [l for l in out["lines"] if "Experience" in l["text"] or "experience" in l["text"]]
    assert xp and xp[0]["right"] == "no lifeforce" and xp[0]["icon"] == "neutral", xp
    assert any("20 decks" in l["right"] for l in lines.values()), [l["right"] for l in lines.values()]
    assert abs(out["value"] - (out["est"] * 0.01 + 20 * 3.5)) < 1e-6
    assert out["advice"] is not None and out["advice"]["decks_counted"]
    con.close()


@test
def starter_mode_without_own_plants_matches_starter():
    m = mdl.fit(synthetic())
    starter = m.to_json({"rolls": [], "fingerprint": []})
    ms = mdl.fit_with_starter([], starter)
    p = synthetic(n=1, seed=7)[0]
    a, b = m.estimate(p)[0], ms.estimate(p)[0]
    assert abs(a / b - 1) < 0.02, (a, b)
    assert ms.mode == "starter" and ms.usable


@test
def starter_moves_with_own_plants():
    m = mdl.fit(synthetic())
    starter = m.to_json({"rolls": [], "fingerprint": []})
    own = synthetic(n=60, seed=3)
    for p in own:
        p["lifeforce"] *= 1.5            # this player's setup gives 50% more
    ms = mdl.fit_with_starter(own, starter)
    cond = {"atlas": 82}
    ratio = ms.base("Vivid", cond) / m.base("Vivid", cond)
    assert 1.3 < ratio < 1.7, ratio


@test
def shipped_starter_loads():
    s = mdl.load_starter()
    assert s and s["n"] >= 30 and s["features"] == mdl.feature_names()


@test
def reader_normalises_resolution():
    img = np.zeros((1080, 1920, 3), np.uint8)
    out, scale = reader.normalize(img)
    assert out.shape[:2] == (1440, 2560) and abs(scale - 4 / 3) < 1e-6
    out, scale = reader.normalize(np.zeros((2160, 3840, 3), np.uint8))
    assert out.shape[:2] == (1440, 2560)


@test
def parse_a_tooltip_reading():
    r = reader.TooltipReading(kind="flora", name="Twiste Vegetation", base="Vivid Roots", rarity="rare")
    r.lines = [reader.TextLine(0, 10, 0, 100, "mod", t) for t in (
        "Guarded by Sacred Fauna",
        "Spawns 6 additional Rare Tier 3 Sacred Fauna",
        "75% increased Sacred Fauna spawn rate",
        "Sacred Fauna have 80% increased Item Rarity",
        "Sacred Fauna drop 112% increased Quantity of Lifeforce")]
    p = mods.parse_tooltip(r)
    assert p.colour == "Vivid", p.colour
    assert p.mod_values().get("extra_rare_t3") == 6, p.mod_values()
    assert p.name == "Twisted Vegetation", p.name


@test
def finds_a_white_plant_tooltip():
    import cv2
    img = cv2.imread(os.path.join(ROOT, "tools", "fixtures", "white_plant_tooltip.png"))
    found = reader.find_tooltips(img)
    assert len(found) == 1 and found[0].kind == "flora" and found[0].frame == "flora_single", found
    t = found[0]
    assert abs((t.x1 - t.x0) - 383) < 6 and abs((t.y1 - t.y0) - 44) < 4, t


@test
def parse_a_white_plant():
    r = reader.TooltipReading(kind="flora", name="VIVID ROOTS", base="", rarity="normal")
    r.lines = [reader.TextLine(0, 10, 0, 100, "mod", "GUARDED BY SACRED FAUNA")]
    p = mods.parse_tooltip(r)
    assert (p.colour, p.base, p.name, p.rarity) == ("Vivid", "Vivid Roots", "Vivid Roots", "normal"), p
    assert not p.problems and not p.mod_values(), (p.problems, p.mod_values())


@test
def settings_round_trip():
    d = tempfile.mkdtemp()
    s = settings_mod.Settings(os.path.join(d, "settings.json"))
    s.update(atlas_drops=82.0, volume=40, tree_lifeforce=12.0, white_map=True, t3_base=5)
    s2 = settings_mod.Settings(os.path.join(d, "settings.json"))
    assert s2["atlas_drops"] == 82.0 and s2["volume"] == 40
    assert s2["tree_lifeforce"] == 12.0 and s2["white_map"] is True and s2["t3_base"] == 5
    assert s2["hotkeys"]["node"]["name"] == "Numpad 1"


if sys.platform == "win32":
    @test
    def windows_modules_import():
        from poe_harvest import app, capture, settings_window, win  # noqa: F401

    @test
    def numpad_keys_match_with_numlock_on_or_off():
        from poe_harvest import win
        hk = settings_mod.NUMPAD_1
        assert win.key_matches(hk, 0x61, 0x4F, 0)           # NumLock on
        assert win.key_matches(hk, 0x23, 0x4F, 0)           # NumLock off: VK_END, same physical key
        assert not win.key_matches(hk, 0x23, 0x4F, 1)       # the real End key (extended)
        assert win.key_matches(hk, 0x61, 0, 0)              # sent by mouse software without a scan code


print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
